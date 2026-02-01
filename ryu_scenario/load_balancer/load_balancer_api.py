import os
from urllib import request
from flask import Flask, jsonify, request
from flask_cors import CORS
import time
import threading
import subprocess
import sys
import json
import signal

class LoadBalancerAPI():
    """
    REST API to bridge the GUI and the SDN Load Balancer.
    Handles the execution of Mininet, Controller startup, and Traffic Generation,
    and exposes the system status to the frontend.
    """
    def __init__(self, load_balancer):
        # Load Balancer Instance
        self.balancer = load_balancer
        
        # Flask App Setup
        self.app = Flask(__name__)
        CORS(self.app)
        self._setup_routes()
        
    def _setup_routes(self):
        
        @self.app.route('/init_mininet', methods=['POST'])
        def init_mininet():
            """
            Executes the Mininet topology in a separate subprocess.
            
            Returns:
                Response: A JSON response containing:
                    - status (str): 'success' or 'error'.
                    - message (str): Description of the result.
            """
            self.balancer.logger.info(" [API] Initializing Mininet Scenario")
            
            try:
                subprocess.Popen(["sudo", "python3", "ryu_scenario/run_scenario.py"])
                return jsonify({"status": "success", "message": "Mininet started successfully"})
            
            except Exception as e:
                self.balancer.logger.error(f" [API] Failed to start Mininet: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500
           
        @self.app.route('/stop_mininet', methods=['POST'])
        def stop_mininet():
            """
            Stops the Mininet scenario and shuts down the application.
            
            Returns:
                Response: A JSON response containing:
                    - status (str): 'success' or 'error'.
                    - message (str): Description of the result.
            """
            self.balancer.logger.info(" [API] Stopping Mininet Scenario")
            
            try:
                subprocess.run("sudo mn -c", shell=True)
                for c_id in list(self.balancer.active_controllers):
                    self.balancer.stop_controller(c_id)
                
                self.balancer.active_controllers.clear()
                self.balancer.current_avg_load = 0
                self.balancer.monitoring_active = False
               
                return jsonify({"status": "success", "message": "Mininet stopped successfully"})
            
            except Exception as e:
                self.balancer.logger.error(f" [API] Failed to stop Mininet: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500 
        
        @self.app.route('/init_controllers', methods=['POST'])
        def init_controllers():
            """
            Spawns the base cluster with one controller instance.
            
            Returns:
                Response: A JSON response containing:
                    - status (str): 'success'.
                    - message (str): Status message with active controller list.
            """
            self.balancer.logger.info(" [API] Starting Controller Cluster")
            self.balancer.scale_up()
            self.balancer.monitoring_active = True
            return jsonify({"status": "success", "message": f" Cluster created. Active Controllers: {sorted(list(self.balancer.active_controllers))}"})
                
        @self.app.route('/scale_up', methods=['POST'])
        def scale_up():
            """
            Creates a new controller instance.

            Returns:
                Response: A JSON response containing:
                    - status (str): 'success'.
                    - message (str): Status message with active controller list.
            """
            self.balancer.logger.info(" [API] Create New Controller")
            self.balancer.scale_up()
            
            return jsonify({"status": "success", "message": f" New controller created. Total Active: {sorted(list(self.balancer.active_controllers))}"})

        @self.app.route('/scale_down', methods=['POST'])
        def scale_down():
            """
            Deletes a controller instance

            Returns:
                Response: A JSON response containing:
                    - status (str): 'success'.
                    - message (str): Status message with active controller list.
            """
            self.balancer.logger.info(" [API] Remove Controller")
            self.balancer.scale_down()
            
            return jsonify({"status": "success", "message": f" Removed controller. Total Active: {sorted(list(self.balancer.active_controllers))}"})

        @self.app.route('/init_balancer', methods=['POST'])
        def init_balancer():
            """
            Initiates the balancing capabilities

            Returns:
                Response: A JSON response containing:
                    - status (str): 'success'.
                    - message (str): Status message indicating the load balancer is active.
            """
            self.balancer.logger.info(" [API]Load Balancer Activated")
            
            self.balancer.update_ovs_connections()
            self.balancer.distribute_switches()
            self.balancer.auto_mode = True 
            
            return jsonify({"status": "success", "message": "Load Balancer is now active"})
        
        @self.app.route('/stop_balancer', methods=['POST'])
        def stop_balancer():
            """
            Stops the load balancer.

            Returns:
                Response: A JSON response containing:
                    - status (str): 'success'.
                    - message (str): Status message indicating the load balancer is stopped.
            """
            self.balancer.logger.info(" [API]Load Balancer Stopped")
            self.balancer.auto_mode = False 
            
            return jsonify({"status": "success", "message": "Load Balancer is now stopped"})

        @self.app.route('/status', methods=['GET'])
        def get_status():
            """
            Returns real-time metrics for the charts and status indicators.
            
            Returns:
                Response: A JSON response containing:
                    - active_controllers (list): List of active controller IDs.
                    - avg_load (float): Current average PPS load per controller.
                    - individual_rates (dict): Map of {controller_id: pps}.
                    - is_scaling (bool): True if the system is in cooldown/scaling state.
                    - max_controllers (int): The configured maximum limit.
                    - auto_mode (bool): True if the load balancer is in automatic mode.
                    - scaling_status_msg (str): Status message for scaling actions.
            """
            return jsonify({
                "active_controllers": sorted(list(self.balancer.active_controllers)),
                "avg_load": round(self.balancer.current_avg_load, 2),
                "individual_rates": self.balancer.current_rates,
                "is_scaling": (time.time() - self.balancer.last_scale_action_time) < self.balancer.COOLDOWN_TIME,
                "max_controllers": self.balancer.MAX_CONTROLLERS,
                "auto_mode": self.balancer.auto_mode,
                "scaling_msg": self.balancer.scaling_status_msg
            })

        @self.app.route('/generate_traffic', methods=['POST'])
        def generate_traffic():
            """
            Launches the traffic generator script on host m_p1.
            
            Returns:
                Response: A JSON response containing:
                    - status (str): 'success' or 'error'.
                    - message (str): Description of the result.
            """
            
            data = request.get_json()
            if not data: data = {}
            pps = data.get('pps', 100)
            duration = data.get('time', 60)

            self.balancer.logger.info(f" [API] Generating Traffic-> {pps} PPS for {duration}s")
            try:
                pid_cmd = "pgrep -f 'mininet:m_p1'"
                pid_bytes = subprocess.check_output(pid_cmd, shell=True)
                pid = pid_bytes.decode('utf-8').strip().split('\n')[0]
                
                if not pid:
                    raise Exception("Host m_p1 PID not found. Is Mininet running?")

                cmd = f"sudo mnexec -a {pid} python3 ryu_scenario/traffic_gen.py {pps} {duration} &"
                subprocess.Popen(cmd, shell=True)
                return jsonify({"status": "success", "message": f"Traffic Injection Started: {pps} PPS for {duration}s on m_p1"})
            
            except Exception as e:
                self.balancer.logger.error(f"Traffic generation failed: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

        
    def run(self):
        """
        Launching Flask Server
        """
        self.app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)