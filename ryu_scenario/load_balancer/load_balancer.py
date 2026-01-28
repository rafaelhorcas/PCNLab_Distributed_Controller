import threading
import requests
import time
import docker
import sys
import subprocess
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from BaseLogger import BaseLogger
from LoadBalancerAPI import LoadBalancerAPI
import csv
import datetime

class RyuLoadBalancer(BaseLogger):
    """
    SDN Load Balancer that manages a cluster of Ryu controllers using Docker.
    It monitors network traffic and scales the number of controllers based on average load.
    """
    def __init__(self,log_level="INFO"):
        # Logger Initialization
        super().__init__(log_name="load_balancer", log_level=log_level)
        
        # --- CONFIGURATION PARAMETERS---
        self.BASE_WS_PORT = 8081                 # Base TCP Port for WebSocket API
        self.BASE_OFP_PORT = 6653                # Base TCP Port for OpenFlow
        self.IMAGE_NAME = "ryu-controller"       # Docker image name

        # Scaling 
        self.MIN_CONTROLLERS = 2                 # Minimum number of controllers to keep alive
        self.MAX_CONTROLLERS = 5                 # Maximum number of controllers allowed
        self.TARGET_LOAD_PER_CONTROLLER = 50     # Avg PPS threshold to scale UP
        self.MIN_LOAD_PER_CONTROLLER = 15        # Avg PPS threshold to scale DOWN
        self.current_avg_load = 0                # Current average load per controller
        self.current_rates = {}                  # Current packet rates per controller

        # Timers
        self.CHECK_INTERVAL = 5                  # How often to check metrics
        self.WARMUP_TIME = 15                    # Time to wait for a new controller to learn topology
        self.COOLDOWN_TIME = 10                  # Time to wait after a scaling action before checking again

        # Global State
        self.active_controllers = set()          # Set of active controller IDs
        self.docker_client = docker.from_env()   # Docker client instance
        self.previous_packet_counts = {}         # Previous packet counts per controller
        self.last_scale_action_time = 0          # Timestamp of last scaling action
        self.CURRENT_GEN_ID = 0                  # Generation ID for role requests
        
        # GUI
        self.monitoring_active = False          # Flag to start monitoring loop
        self.auto_mode = False
        self.last_event_msg = "System Idle - Waiting for Mininet"
        self.api = LoadBalancerAPI(self)
        
        # Tests
        self.start_time = time.time()
        self.csv_filename = "experiment_results.csv"
        with open(self.csv_filename, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'elapsed_s', 'num_controllers', 'total_pps', 'avg_load', 'is_scaling'])

    # --- OVS FUNCTIONS ---
    def get_all_switches(self):
        """
        Retrieves the list of active switches from the OVS system.
        
        Returns:
            list: List of strings representing switch names.
        """
        try:
            result = subprocess.check_output(["ovs-vsctl", "list-br"], text=True)
            return [line.strip() for line in result.splitlines() if line.strip()]
        except:
            return []

    def update_ovs_connections(self):
        """
        Updates the OVS configuration to connect every switch to all active controllers.
        
        Returns:
            None
        """
        switches = self.get_all_switches()
        if not switches:
            return

        if not self.active_controllers:
            cmd_type = "del-controller"
            target_str = ""
        else:
            cmd_type = "set-controller"
            targets = []
            for i in sorted(list(self.active_controllers)):
                port = self.BASE_OFP_PORT + i
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

    def start_controller(self,controller_ID):
        """
        Starts a new Ryu controller Docker container.
        Removes any existing container with the same ID and launches a new one.
        
        Args:
            controller_ID (int): Unique identifier for the controller instance.
            
        Returns:
            bool: True if the controller started successfully, False otherwise.
        """
        # Create container name and port mappings
        name = f"ryu_{controller_ID}"
        ws_port = self.BASE_WS_PORT + controller_ID
        ofp_port = self.BASE_OFP_PORT + controller_ID
        try:
            # Remove old container if exists
            try:
                old = self.docker_client.containers.get(name)
                old.remove(force=True)
            except: pass
            
            # Run new container
            self.docker_client.containers.run(
                image=self.IMAGE_NAME,
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
            self.active_controllers.add(controller_ID)
            self.logger.info(f"\n [DOCKER] Created {name} | OFP_PORT: {ofp_port} | WS_PORT: {ws_port}")
            return True
        
        except Exception as e:
            self.logger.info(f" [ERROR] Failed to start {name}: {e}")
            return False

    def stop_controller(self,controller_ID):
        """
        Stops and removes a specific Ryu controller Docker container.
        
        Args:
            controller_ID (int): Unique identifier for the controller instance.
        """
        name = f"ryu_{controller_ID}"
        try:
            c = self.docker_client.containers.get(name)
            c.stop()
            c.remove()
            if controller_ID in self.active_controllers:
                self.active_controllers.discard(controller_ID)
                
            self.logger.info(f" [DOCKER] Deleted {name}")
            
        except Exception as e:
            self.logger.info(f" [ERROR] Stopping {name}: {e}")

        self.active_controllers.discard(controller_ID)

    def cleanup(self):
        """
        Stops and removes all active controller containers upon program exit.
        
        Returns:
            None
        """
        self.logger.info("\n [EXIT] Cleaning up containers...")
        for i in list(self.active_controllers):
            self.stop_controller(i)
        sys.exit(0)

    # --- METRICS & SCALING LOGIC ---

    def get_total_traffic_rate(self):
        """
        Calculates the aggregate rate of new Packet-In messages across all controllers.
        Polls the /metrics endpoint of each active controller and computes the delta from the previous observation to determine current PPS.
        
        Returns:
            total_new_packets (int): Sum of new packets across all controllers.
            individual_rates (dict): Map of {controller_id: rate}.
        """
        
        total_new_packets = 0
        current_counts = {}
        individual_rates = {} 
        
        for c_id in list(self.active_controllers):
            ws_port = self.BASE_WS_PORT + c_id
            url = f"http://localhost:{ws_port}/metrics"
            try:
                # Short timeout to avoid blocking the loop
                r = requests.get(url, timeout=1).json()
                count = r.get('packet_in_count', 0)
                
                current_counts[c_id] = count
                
                # Calculate Delta (Current - Previous)
                prev = self.previous_packet_counts.get(c_id, count)
                delta = count - prev
                if delta < 0: delta = 0 # Handle controller restarts
                
                individual_rates[c_id] = delta
                total_new_packets += delta
                
            except requests.exceptions.RequestException:
                individual_rates[c_id] = -1 # Mark as unreachable
                pass
        
        # Update history for next iteration
        self.previous_packet_counts = current_counts.copy()
        
        return total_new_packets, individual_rates

    def scale_up(self):
        """
        Spawns an additional controller to handle high network traffic.
        Sequence: Start -> Update OVS -> Warmup -> Distribute.
        
        Returns:
            None
        """
        
        if len(self.active_controllers) >= self.MAX_CONTROLLERS:
            self.logger.info(" [WARN] MAX CONTROLLERS REACHED. Cannot scale up.")
            return

        # Calculate next available ID
        new_id = max(self.active_controllers) + 1 if self.active_controllers else 0

        if self.start_controller(new_id):
            # 1. Connect to OVS immediately to receive LLDP
            self.logger.info("   -> Updating OVS connections...")
            self.update_ovs_connections()
            
            # 2. Warm-up phase (Topology Discovery)
            self.logger.info(f"   -> Waiting {self.WARMUP_TIME}s for topology discovery (Warm-up)...")
            time.sleep(self.WARMUP_TIME)

            # 3. Rebalance load
            self.logger.info("   -> Re-distributing switches...")
            self.distribute_switches()
            
            self.last_scale_action_time = time.time()

    def scale_down(self):
        """
        Removes a controller from the cluster when network load is low.
        Sequence: Remove from list -> Redistribute switches -> Stop Container.
        
        Returns:
            None
        """
        
        if len(self.active_controllers) <= self.MIN_CONTROLLERS:
            self.logger.info(" [WARN] MIN CONTROLLERS REACHED. Cannot scale down.")
            return

        # Select victim (Highest ID)
        controller_id = max(self.active_controllers)

        # 1. Remove from active set so distribute_switches ignores it
        self.active_controllers.discard(controller_id)
        
        # 2. Move switches to remaining controllers BEFORE killing the container
        self.logger.info(f"   -> Reassigning switches from ryu_{controller_id} to others...")
        self.distribute_switches()
        
        # 3. Wait a moment for roles to propagate
        time.sleep(2) 
        
        # 4. Stop the container
        self.stop_controller(controller_id)
        
        last_scale_action_time = time.time()
        self.logger.info(f"   -> COMPLETE. Active controllers: {len(self.active_controllers)}")
        
    def distribute_switches(self):
        """
        Assigns Master/Slave roles to controllers using Round Robin.
        
        Returns:
            None
        """
        self.CURRENT_GEN_ID += 1
        
        switches = self.get_all_switches()
        controllers = sorted(list(self.active_controllers))

        if not controllers or not switches: return
        
        self.logger.info(f"--- Rebalancing {len(switches)} switches among {len(controllers)} controllers ---")

        for idx, sw in enumerate(switches):
            # Round Robin Logic
            assigned_controller = controllers[idx % len(controllers)]
        
            dpid = int(sw.replace("s", ""))
            
            # Send Role Request to ALL active controllers for this switch
            for c_id in controllers:
                role = "MASTER" if c_id == assigned_controller else "SLAVE"
                url = f"http://localhost:{self.BASE_WS_PORT + c_id}/role"
                try:
                    requests.post(url, json={
                        "dpid": dpid,
                        "role": role,
                        "generation_id": self.CURRENT_GEN_ID
                    }, timeout=1)
                except requests.exceptions.RequestException as e:
                    self.logger.info(f"Error assigning switch {sw} to controller ryu_{c_id}: {e}")

    # --- MAIN LOOP ---

    def run(self):
        """
        The main monitoring and decision-making loop of the load balancer.
        Initializes the base cluster and periodically evaluates average load per controller to trigger scaling actions.

        Returns:
            None
        """
        self.logger.info("Starting SDN Auto-Scaling Load Balancer")
        flask_thread = threading.Thread(target=self.api.run, daemon=True)
        flask_thread.start()

        # 1. Initial State (Start minimum controllers)
        #self.start_controller(0)
        #self.start_controller(1)
        
        #self.logger.info("Waiting for Mininet initialization...")
        #time.sleep(15)
        
        #self.logger.info("Initial OVS Update...")
        #self.update_ovs_connections()
        #self.distribute_switches()

        last_scale_action_time = time.time()

        # 2. Monitoring Loop
        try:
            while True:
                if not self.monitoring_active:
                    time.sleep(1)
                    continue
                
                time.sleep(self.CHECK_INTERVAL)

                # Get Metrics
                total_packets, rates_map = self.get_total_traffic_rate()
                num_active = len(self.active_controllers)
                self.current_avg_load = total_packets / (num_active if num_active > 0 else 1)
                self.current_rates = rates_map
                is_scaling = (time.time() - self.last_scale_action_time) < self.COOLDOWN_TIME

                # --- 3. GUARDAR EN EL CSV ---
                elapsed = int(time.time() - self.start_time)
                with open(self.csv_filename, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        datetime.datetime.now().strftime("%H:%M:%S"),
                        elapsed,
                        num_active,
                        total_packets,
                        round(self.current_avg_load, 2),
                        1 if is_scaling else 0
                    ])
                if self.auto_mode:
                    # Cooldown check
                    if (time.time() - self.last_scale_action_time) < self.COOLDOWN_TIME:
                            continue

                    if self.current_avg_load > self.TARGET_LOAD_PER_CONTROLLER:
                        self.logger.warning(f"AUTO: High Load ({self.current_avg_load:.1f}). Scaling UP.")
                        self.scale_up()
                    
                    elif self.current_avg_load < self.MIN_LOAD_PER_CONTROLLER and num_active > self.MIN_CONTROLLERS:
                        self.logger.warning(f"AUTO: Low Load ({self.current_avg_load:.1f}). Scaling DOWN.")
                        self.scale_down()
                else:
                    self.logger.info("   [OK] System Stable.")

        except KeyboardInterrupt:
            self.cleanup()

if __name__ == "__main__":
    balancer = RyuLoadBalancer()
    balancer.run()