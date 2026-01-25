import requests
import time
import docker
import sys
import subprocess

# --- CONFIGURATION ---
BASE_WS_PORT = 8081
BASE_OFP_PORT = 6653
IMAGE_NAME = "ryu-controller"
active_controllers = set()
docker_client = docker.from_env()

# Load difference threshold to avoid frequent leader changes
LOAD_THRESHOLD = 10

# Global generation ID (must be monotonically increasing for OVS)
CURRENT_GEN_ID = 0

# --- OVS FUNCTIONS ---
def get_all_switches():
    try:
        result = subprocess.check_output(["ovs-vsctl", "list-br"], text=True)
        print(f"Detected switches: {result}")
        return [line.strip() for line in result.splitlines() if line.strip()]
    except:
        return []

def update_ovs_connections():
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

    for sw in switches:
        if target_str:
            cmd = f"ovs-vsctl set bridge {sw} protocols=OpenFlow13 && " \
                  f"ovs-vsctl {cmd_type} {sw} {target_str}"
        else:
            cmd = f"ovs-vsctl {cmd_type} {sw}"
            
        subprocess.run(cmd, shell=True)

# --- DOCKER FUNCTIONS ---
def start_controller(controller_ID):
    name = f"ryu_{controller_ID}"
    ws_port = BASE_WS_PORT + controller_ID
    ofp_port = BASE_OFP_PORT + controller_ID
    
    try:
        try:
            old = docker_client.containers.get(name)
            old.remove(force=True)
        except: pass
        docker_client.containers.run(
            image=IMAGE_NAME,
            name=name,
            detach=True,
            network="host",
            command=[
                "ryu-manager","controller.py",
                "--ofp-tcp-listen-port", str(ofp_port),
                "--wsapi-port", str(ws_port),
                "--observe-links"
            ]
        )
        active_controllers.add(controller_ID)
        update_ovs_connections()
        print(f"\n Created {name} | OFP_PORT: {ofp_port} | WS_PORT: {ws_port}")
    
    except Exception as e:
        print(f"Error: {e}")
        return

def stop_controller(controller_ID):
    name = f"ryu_{controller_ID}"
    try:
        c = docker_client.containers.get(name)
        c.stop()
        c.remove()

        if controller_ID in active_controllers:
            active_controllers.discard(controller_ID)
            update_ovs_connections()
            
        print(f"Deleted {name}")
    except Exception as e:
        print(f"Error: {e}")

    active_controllers.discard(controller_ID)

def cleanup():
    print("\n Cleaning...")
    for i in list(active_controllers):
        stop_controller(i)
    sys.exit(0)

def get_metrics():
    """
    Query each controller for load metrics.
    Expected response:
      {
        "packet_in_count": int,
        "switches": [dpid, ...]
      }
    """
    data = {}
    for c_id in list(active_controllers):
        name = f"ryu_{c_id}"
        ws_port = BASE_WS_PORT + c_id
        url = f"http://localhost:{ws_port}"
        
        try:
            r = requests.get(f"{url}/metrics", timeout=2).json()
            data[name] = r
            print(f"[{name}] OK - Packet-In count: {r.get('packet_in_count', 0)}")
        except requests.exceptions.RequestException:
            print(f"[{name}] DOWN - No response")
            data[name] = None
    return data
                
def distribute_switches():
    
    global CURRENT_GEN_ID
    CURRENT_GEN_ID += 1
    
    switches = get_all_switches()
    controllers = sorted(list(active_controllers))

    if not controllers or not switches: return
    # Round Robin assignment
    for idx, sw in enumerate(switches):
        assigned_controller = controllers[idx % len(controllers)]
       
        dpid = int(sw.replace("s", ""))
        for c_id in controllers:
            role = "MASTER" if c_id == assigned_controller else "SLAVE"
            url = f"http://localhost:{BASE_WS_PORT + c_id}/role"
            try:
                requests.post(url, json={
                    "dpid": dpid,
                    "role": role,
                    "generation_id": CURRENT_GEN_ID
                }, timeout=2)
            except requests.exceptions.RequestException as e:
                print(f"Error assigning switch {sw} to controller ryu_{c_id}: {e}")
            
        print(f"Switch {sw} assigned to controller ryu_{assigned_controller}")

def run_balancer():
    print("Starting SDN controller Load Balancer")

    # Default start with 2 controllers
    start_controller(0)
    start_controller(1)
    time.sleep(15)
    distribute_switches()

    try:
        while True:
            print("\nChecking controller metrics...")
            metrics = get_metrics()
            print(f"Collected metrics: {metrics}")
            time.sleep(5)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    run_balancer()
