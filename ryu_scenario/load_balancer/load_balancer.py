import threading
import requests
import time
import docker
import sys
import subprocess
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from BaseLogger import BaseLogger
from load_balancer_api import LoadBalancerAPI

class RyuLoadBalancer(BaseLogger):
    """
    SDN Load Balancer that manages a cluster of Ryu controllers using Docker.
    It monitors network traffic and scales the number of controllers based on average load.
    """
    def __init__(self,log_level="INFO"):
        # Logger Initialization
        super().__init__(log_name="load_balancer", log_level=log_level)
        
        # --- CONFIGURATION PARAMETERS---
        self.BASE_WS_PORT = 8081                # Base TCP Port for WebSocket API
        self.BASE_OFP_PORT = 6653               # Base TCP Port for OpenFlow
        self.IMAGE_NAME = "ryu-controller"      # Docker image name

        # Scaling 
        self.MIN_CONTROLLERS = 2                # Minimum number of controllers to keep alive
        self.MAX_CONTROLLERS = 5                # Maximum number of controllers allowed
        self.TARGET_LOAD_PER_CONTROLLER = 50    # Avg PPS threshold to scale UP
        self.MIN_LOAD_PER_CONTROLLER = 15       # Avg PPS threshold to scale DOWN
        self.current_avg_load = 0               # Current average load per controller
        self.current_rates = {}                 # Current packet rates per controller
        self.previous_metrics = {}              # Previous packet counts per controller
        self.last_scale_action_time = 0         # Timestamp of last scaling action

        # Timers
        self.CHECK_INTERVAL = 1                 # How often to check metrics
        self.WARMUP_TIME = 5                    # Time to wait for a new controller to learn topology
        self.COOLDOWN_TIME = 10                 # Time to wait after a scaling action before checking again

        # Global State
        self.active_controllers = set()         # Set of active controller IDs
        self.docker_client = docker.from_env()  # Docker client instance

        self.CURRENT_GEN_ID = 0                 # Generation ID for role requests
        self.is_scaling = False                 # Flag indicating if a scaling action is in progress
        self.start_time = time.time()           # Timestamp when the balancer started
        
        # GUI
        self.monitoring_active = False          # Flag to start monitoring loop
        self.auto_mode = False
        self.last_event_msg = "System Idle - Waiting for Mininet"
        self.api = LoadBalancerAPI(self)

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
            self.logger.info(f" [DOCKER] Created {name} | OFP_PORT: {ofp_port} | WS_PORT: {ws_port}")
            return True
        
        except Exception as e:
            self.logger.error(f" [ERROR] Failed to start {name}: {e}")
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
            self.logger.error(f" [ERROR] Stopping {name}: {e}")

        self.active_controllers.discard(controller_ID)

    def cleanup(self):
        """
        Stops and removes all active controller containers upon program exit.
        """
        self.logger.info(" [DOCKER] Cleaning up containers...")
        for i in list(self.active_controllers):
            self.stop_controller(i)
        sys.exit(0)

    # --- METRICS & SCALING LOGIC ---
    
    def _fetch_pkt_in_count(self, controller_ID):
        """        
        Fetches the total number of Packet-In messages processed by a specific controller.       

        Args:
            controller_ID (int): Unique identifier for the controller instance.

        Returns:
            int: Value of 'packet_in_count' from the controller's /metrics endpoint.
        """
        ws_port = self.BASE_WS_PORT + controller_ID
        url = f"http://localhost:{ws_port}/metrics"
        
        try:
            r = requests.get(url, timeout=0.5).json()
            return r.get('packet_in_count', 0)
        
        except (requests.exceptions.RequestException, ValueError):
            return None
                
    def _calculate_pps(self, controller_ID, current_count):
        """
        Calculates the rate of Packet-In messages for a specific controller.

        Args:
            controller_ID (int): Unique identifier for the controller instance.
            current_count (int): Current packet count from the controller.

        Returns:
            float: Rate of packets per second.
        """
        now = time.time()       
        prev_time, prev_count = self.previous_metrics.get(controller_ID, (now - self.CHECK_INTERVAL, 0))
        
        # Avoid division by zero
        time_delta = now - prev_time
        if time_delta <= 0: time_delta = 0.001
        # Calculate packet delta
        packet_delta = current_count - prev_count
        if packet_delta < 0: packet_delta = current_count
        # Store current metrics for next calculation
        self.previous_metrics[controller_ID] = (now, current_count)
        
        return round(packet_delta / time_delta, 2)
    
    def _handle_failover(self, dead_controllers):
        """
        Check if any controllers are dead and handle their removal.

        Args:
            dead_controllers (list): List of controller IDs that are unresponsive.
        """
        if not dead_controllers: return
        
        for d_id in dead_controllers:
            self.active_controllers.discard(d_id)
            self.previous_metrics.pop(d_id, None)
            
        self.update_ovs_connections()
        self.distribute_switches()
        self.last_scale_action_time = time.time()

    def get_traffic_metrics(self):
        """
        Calculates the aggregate rate of new Packet-In messages across all controllers.
        Polls the /metrics endpoint of each active controller and computes the delta from the previous observation to determine current PPS.
        
        Returns:
            total_new_packets (int): Sum of new packets across all controllers.
            individual_rates (dict): Map of {controller_id: rate}.
        """
        total_pps = 0
        controller_rates = {}
        dead_controllers = []
        
        # Poll each active controller
        for c_id in list(self.active_controllers):
            try:
                # Extract the count of Packet-In messages from the controller
                packet_count = self._fetch_pkt_in_count(c_id)
                
                # If controller is unreachable, mark as dead
                if packet_count is None:
                    dead_controllers.append(c_id)
                    controller_rates[c_id] = -1
                    continue
                
                # Calculate packet rate since last check
                pps = self._calculate_pps(c_id, packet_count)
                controller_rates[c_id] = pps
                total_pps += pps
                
            except Exception as e:
                continue
            
        self._handle_failover(dead_controllers)
        
        return total_pps, controller_rates
            
    def scale_up(self):
        """
        Spawns an additional controller and redistributes switches.
        """
        try:
            if len(self.active_controllers) >= self.MAX_CONTROLLERS:
                self.logger.warning(" [WARN] MAX CONTROLLERS REACHED. Cannot scale up.")
                return

            # Calculate next available ID
            new_id = max(self.active_controllers) + 1 if self.active_controllers else 0

            if self.start_controller(new_id):
                self.logger.debug("Updating OVS connections...")
                self.update_ovs_connections()
                
                self.logger.debug(f"Waiting Warm-up time ({self.WARMUP_TIME}s) ")
                time.sleep(self.WARMUP_TIME)
                
                self.logger.debug("Re-distributing switches...")
                self.distribute_switches()
                
                self.last_scale_action_time = time.time()
                
        except Exception as e:
            self.logger.error(f" [ERROR] In scale_up thread: {e}")
            
        finally:
            self.is_scaling = False
            self.last_scale_action_time = time.time()
            self.logger.info(f" [INFO] New Controller READY")

    def scale_down(self):
        """
        Removes a controller from the cluster and redistributes switches.
        """
        try:
            if len(self.active_controllers) <= self.MIN_CONTROLLERS:
                self.logger.warning(" [WARN] MIN CONTROLLERS REACHED. Cannot scale down.")
                return

            # Select victim (Highest ID)
            controller_id = max(self.active_controllers)
            self.active_controllers.discard(controller_id)
            
            self.logger.debug(f"Reassigning switches from ryu_{controller_id} to others...")
            self.distribute_switches()
            time.sleep(1) 

            self.stop_controller(controller_id)
            
            self.last_scale_action_time = time.time()
            
            self.logger.debug(f"COMPLETE. Active controllers: {len(self.active_controllers)}")
            
        except Exception as e:
            self.logger.error(f" [ERROR] In scale_down thread: {e}")
            
        finally:
            self.is_scaling = False
            self.last_scale_action_time = time.time()
            self.logger.info(f" [INFO] Controller REMOVED")
            
    def distribute_switches(self):
        """
        Assigns Master/Slave roles to controllers using Round Robin.
        """
        self.CURRENT_GEN_ID += 1
        
        switches = self.get_all_switches()
        controllers = sorted(list(self.active_controllers))

        if not controllers or not switches: return
        
        self.logger.info(f" [INFO] Rebalancing {len(switches)} switches among {len(controllers)} controllers ---")

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
        """
        # Start the Flask API in a separate thread
        self.logger.info("Starting SDN Auto-Scaling Load Balancer")
        flask_thread = threading.Thread(target=self.api.run, daemon=True)
        flask_thread.start()

        # Monitoring Loop
        try:
            while True:
                if not self.monitoring_active:
                    time.sleep(1)
                    continue
                
                time.sleep(self.CHECK_INTERVAL)

                # Get Metrics
                total_pps, controller_rates = self.get_traffic_metrics()
                self.current_rates = controller_rates
                
                # Calculate Average Load
                num_active = len(self.active_controllers)
                if num_active > 0:
                    self.current_avg_load = total_pps / num_active
                else:
                    self.current_avg_load = 0
                    
                # Implement Scaling Logic
                if self.auto_mode:
                    # Check if the cooldwown period has passed
                    cooldown_ok = (time.time() - self.last_scale_action_time) > self.COOLDOWN_TIME
                    if not self.is_scaling and cooldown_ok:
                        # Scaling UP and DOWN conditions
                        if self.current_avg_load > self.TARGET_LOAD_PER_CONTROLLER:
                            if num_active < self.MAX_CONTROLLERS:
                                self.logger.warning(f" [AUTO] LOAD {self.current_avg_load:.1f} > TARGET. SCALING UP.")
                                self.is_scaling = True
                                self.last_scale_action_time = time.time()
                                threading.Thread(target=self.scale_up).start()
                            
                        elif self.current_avg_load < self.MIN_LOAD_PER_CONTROLLER and num_active > self.MIN_CONTROLLERS:
                            self.logger.warning(f" [AUTO] LOAD {self.current_avg_load:.1f} < MIN. SCALING DOWN.")
                            self.is_scaling = True 
                            self.last_scale_action_time = time.time()
                            threading.Thread(target=self.scale_down).start()

        except KeyboardInterrupt:
            self.cleanup()

if __name__ == "__main__":
    balancer = RyuLoadBalancer()
    balancer.run()