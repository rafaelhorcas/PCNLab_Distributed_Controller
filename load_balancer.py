import requests
import time
import docker
import sys
import subprocess
import os

# --- CONFIGURATION ---
BASE_WS_PORT = 8081
BASE_OFP_PORT = 6653
IMAGE_NAME = "ryu-controller"

# Scaling Parameters
MIN_CONTROLLERS = 2           # Minimum number of controllers to keep alive
MAX_CONTROLLERS = 5           # Maximum resources allowed
TARGET_LOAD_PER_CONTROLLER = 50   # Avg PPS threshold to scale UP (Add controller)
MIN_LOAD_PER_CONTROLLER = 15      # Avg PPS threshold to scale DOWN (Remove controller)

# Timers (Seconds)
CHECK_INTERVAL = 5            # How often to check metrics
WARMUP_TIME = 15              # Time to wait for a new controller to learn topology
COOLDOWN_TIME = 10            # Time to wait after a scaling action before checking again

# Global State
active_controllers = set()
docker_client = docker.from_env()
previous_packet_counts = {} 
last_scale_action_time = 0 
CURRENT_GEN_ID = 0

# --- OVS FUNCTIONS ---
def get_all_switches():
    """
    Retrieves the list of active bridges (switches) from OVS.
    """
    try:
        result = subprocess.check_output(["ovs-vsctl", "list-br"], text=True)
        return [line.strip() for line in result.splitlines() if line.strip()]
    except:
        return []

def update_ovs_connections():
    """
    Updates the OVS configuration to connect every switch to ALL active controllers.
    """
    switches = get_all_switches()
    if not switches:
        return
    
    if not active_controllers:
        cmd_type = "del-controller"
        target_str = ""
    else:
        cmd_type = "set-controller"
        targets = []
        for i in sorted(list(active_controllers)):
            port = BASE_OFP_PORT + i
            targets.append(f"tcp:127.0.0.1:{port}")
        target_str = " ".join(targets)

    # Apply configuration to each switch
    for sw in switches:
        if target_str:
            # Using timeout to prevent blocking if OVS is busy
            cmd = f"ovs-vsctl --timeout=5 set bridge {sw} protocols=OpenFlow13 && " \
                  f"ovs-vsctl --timeout=5 {cmd_type} {sw} {target_str}"
        else:
            cmd = f"ovs-vsctl --timeout=5 {cmd_type} {sw}"
            
        subprocess.run(cmd, shell=True)

# --- DOCKER FUNCTIONS ---
def start_controller(controller_ID):
    """
    Starts a new Docker container for the Ryu controller.
    Mounts the local Python files so the container uses the latest code.
    """
    name = f"ryu_{controller_ID}"
    ws_port = BASE_WS_PORT + controller_ID
    ofp_port = BASE_OFP_PORT + controller_ID
    
    # Get current working directory to mount volumes
    cwd = os.getcwd() 
    
    try:
        # Remove old container if exists
        try:
            old = docker_client.containers.get(name)
            old.remove(force=True)
        except: pass
        
        # Run new container
        docker_client.containers.run(
            image=IMAGE_NAME,
            name=name,
            detach=True,
            network="host",
            # Mount local code to container
            volumes={
                f"{cwd}/controller.py": {'bind': '/app/controller.py', 'mode': 'ro'},
                f"{cwd}/controllerRESTAPI.py": {'bind': '/app/controllerRESTAPI.py', 'mode': 'ro'}
            },
            command=[
                "ryu-manager","controller.py",
                "--ofp-tcp-listen-port", str(ofp_port),
                "--wsapi-port", str(ws_port),
                "--observe-links"
            ]
        )
        active_controllers.add(controller_ID)
        print(f"\n [DOCKER] Created {name} | OFP_PORT: {ofp_port} | WS_PORT: {ws_port}")
        return True
    
    except Exception as e:
        print(f" [ERROR] Failed to start {name}: {e}")
        return False

def stop_controller(controller_ID):
    """
    Stops and removes a specific controller container.
    """
    name = f"ryu_{controller_ID}"
    try:
        c = docker_client.containers.get(name)
        c.stop()
        c.remove()

        # Just in case, update connections if removed manually, 
        # though scale_down handles the logic usually.
        if controller_ID in active_controllers:
            active_controllers.discard(controller_ID)
            
        print(f" [DOCKER] Deleted {name}")
    except Exception as e:
        print(f" [ERROR] Stopping {name}: {e}")

    active_controllers.discard(controller_ID)

def cleanup():
    """
    Stops all controllers on exit.
    """
    print("\n [EXIT] Cleaning up containers...")
    for i in list(active_controllers):
        stop_controller(i)
    sys.exit(0)

# --- METRICS & SCALING LOGIC ---

def get_total_traffic_rate():
    """
    Calculates the rate of NEW packets received by all controllers since the last check.
    Returns:
        total_new_packets (int): Sum of new packets across all controllers.
        individual_rates (dict): Map of {controller_id: rate}.
    """
    global previous_packet_counts
    
    total_new_packets = 0
    current_counts = {}
    individual_rates = {} 
    
    for c_id in list(active_controllers):
        ws_port = BASE_WS_PORT + c_id
        url = f"http://localhost:{ws_port}/metrics"
        try:
            # Short timeout to avoid blocking the loop
            r = requests.get(url, timeout=1).json()
            count = r.get('packet_in_count', 0)
            
            current_counts[c_id] = count
            
            # Calculate Delta (Current - Previous)
            prev = previous_packet_counts.get(c_id, count)
            delta = count - prev
            if delta < 0: delta = 0 # Handle controller restarts
            
            individual_rates[c_id] = delta
            total_new_packets += delta
            
        except requests.exceptions.RequestException:
            individual_rates[c_id] = -1 # Mark as unreachable
            pass
    
    # Update history for next iteration
    previous_packet_counts = current_counts.copy()
    
    return total_new_packets, individual_rates

def scale_up():
    """
    Adds a new controller to share the load.
    Sequence: Start -> Update OVS -> Warmup -> Distribute.
    """
    global last_scale_action_time
    
    if len(active_controllers) >= MAX_CONTROLLERS:
        print(" [WARN] MAX CONTROLLERS REACHED. Cannot scale up.")
        return

    # Calculate next available ID
    new_id = max(active_controllers) + 1 if active_controllers else 0
    print(f"\n[SCALING UP] High load detected! Spawning ryu_{new_id}...")

    if start_controller(new_id):
        # 1. Connect to OVS immediately to receive LLDP
        print("   -> Updating OVS connections...")
        update_ovs_connections()
        
        # 2. Warm-up phase (Topology Discovery)
        print(f"   -> Waiting {WARMUP_TIME}s for topology discovery (Warm-up)...")
        time.sleep(WARMUP_TIME)
        
        # 3. Rebalance load
        print("   -> Re-distributing switches...")
        distribute_switches()
        
        last_scale_action_time = time.time()

def scale_down():
    """
    Removes a controller when load is low.
    Sequence: Remove from list -> Redistribute switches -> Stop Container.
    """
    global last_scale_action_time
    
    if len(active_controllers) <= MIN_CONTROLLERS:
        # print("Min controllers reached.")
        return

    # Select victim (Highest ID)
    victim_id = max(active_controllers)
    print(f"\n[SCALING DOWN] Low load detected! Removing ryu_{victim_id}...")

    # 1. Remove from active set so distribute_switches ignores it
    active_controllers.discard(victim_id)
    
    # 2. Move switches to remaining controllers BEFORE killing the container
    print(f"   -> Reassigning switches from ryu_{victim_id} to others...")
    distribute_switches()
    
    # 3. Wait a moment for roles to propagate
    time.sleep(2) 
    
    # 4. Stop the container
    stop_controller(victim_id)
    
    last_scale_action_time = time.time()
    print(f"   -> COMPLETE. Active controllers: {len(active_controllers)}")

def distribute_switches():
    """
    Assigns Master/Slave roles to controllers using Round Robin.
    """
    global CURRENT_GEN_ID
    CURRENT_GEN_ID += 1
    
    switches = get_all_switches()
    controllers = sorted(list(active_controllers))

    if not controllers or not switches: return
    
    print(f"--- Rebalancing {len(switches)} switches among {len(controllers)} controllers ---")

    for idx, sw in enumerate(switches):
        # Round Robin Logic
        assigned_controller = controllers[idx % len(controllers)]
       
        dpid = int(sw.replace("s", ""))
        
        # Send Role Request to ALL active controllers for this switch
        for c_id in controllers:
            role = "MASTER" if c_id == assigned_controller else "SLAVE"
            url = f"http://localhost:{BASE_WS_PORT + c_id}/role"
            try:
                requests.post(url, json={
                    "dpid": dpid,
                    "role": role,
                    "generation_id": CURRENT_GEN_ID
                }, timeout=1)
            except requests.exceptions.RequestException as e:
                print(f"Error assigning switch {sw} to controller ryu_{c_id}: {e}")

# --- MAIN LOOP ---

def run_balancer():
    global last_scale_action_time
    print("Starting SDN Auto-Scaling Load Balancer")

    # 1. Initial State (Start minimum controllers)
    start_controller(0)
    start_controller(1)
    
    print("Waiting for Mininet initialization...")
    time.sleep(15)
    
    print("Initial OVS Update...")
    update_ovs_connections()
    distribute_switches()

    last_scale_action_time = time.time()

    # 2. Monitoring Loop
    try:
        while True:
            time.sleep(CHECK_INTERVAL)
            
            # Cooldown check
            if (time.time() - last_scale_action_time) < COOLDOWN_TIME:
                print(f"Cooldown active... ({int(time.time() - last_scale_action_time)}s / {COOLDOWN_TIME}s)")
                continue

            # Get Metrics
            total_packets, rates_map = get_total_traffic_rate()
            num_active = len(active_controllers)
            
            if num_active == 0: num_active = 1 # Avoid division by zero
            
            # --- AVERAGE LOAD CALCULATION ---
            # This ensures smooth scaling. We look at load PER CONTROLLER, not total.
            avg_load = total_packets / num_active
            
            # Formatting status output
            details_str = " ".join([f"[Ryu_{cid}: {val}]" for cid, val in sorted(rates_map.items())])
            print(f"Status: {details_str}")
            print(f"--> SYSTEM TOTAL: {total_packets} pkts | AVG LOAD: {avg_load:.1f} (Target: {TARGET_LOAD_PER_CONTROLLER})")
            
            # Decision Logic
            if avg_load > TARGET_LOAD_PER_CONTROLLER:
                print(f"   [!] Average load ({avg_load:.1f}) > Target ({TARGET_LOAD_PER_CONTROLLER}) -> SCALING UP")
                scale_up()
                get_total_traffic_rate() # Reset metrics to avoid double triggers
                
            elif avg_load < MIN_LOAD_PER_CONTROLLER and num_active > MIN_CONTROLLERS:
                print(f"   [!] Average load ({avg_load:.1f}) < Min ({MIN_LOAD_PER_CONTROLLER}) -> SCALING DOWN")
                scale_down()
                get_total_traffic_rate() # Reset metrics
            
            else:
                print("   [OK] System Stable.")

    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    run_balancer()