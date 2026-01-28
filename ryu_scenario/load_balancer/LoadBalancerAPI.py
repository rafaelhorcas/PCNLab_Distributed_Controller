import os
from urllib import request
from flask import Flask, jsonify, request
from flask_cors import CORS
import time
import threading
import subprocess
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from BaseLogger import BaseLogger
import json

class LoadBalancerAPI(BaseLogger):
    def __init__(self, load_balancer, log_level="INFO"):
        """
        REST API to bridge the Web Dashboard and the SDN Load Balancer.
        Handles the execution of Mininet, Controller startup, and Traffic Generation.
        """
        BaseLogger.__init__(self, log_name="load_balancerAPI", log_level="INFO")
        self.balancer = load_balancer
        self.app = Flask(__name__)
        CORS(self.app)
        self._setup_routes()
        
    def _setup_routes(self):
        
        @self.app.route('/init_mininet', methods=['POST'])
        def init_mininet():
            """Executes the run_scenario.py script to build the topology."""
            self.balancer.logger.info("Initializing Mininet Scenario")
            try:
                subprocess.Popen(["sudo", "python3", "ryu_scenario/run_scenario.py"])
                return jsonify({"status": "success", "message": "Mininet started successfully"})
            except Exception as e:
                self.balancer.logger.error(f"Failed to start Mininet: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500
        
        @self.app.route('/init_controllers', methods=['POST'])
        def init_controllers():
            """Spawns the base cluster (ryu_0 and ryu_1)."""
            self.balancer.logger.info("Web Dashboard triggered: Starting Base Controllers")
            # We call the methods already defined in your Load Balancer
            self.balancer.start_controller(0)
            self.balancer.start_controller(1)
            return jsonify({"status": "success", "active": sorted(list(self.balancer.active_controllers))})
                
        @self.app.route('/scale_up', methods=['POST'])
        def scale_up():
            self.balancer.logger.info("Manual Scale UP (+)")
            self.balancer.scale_up()
            count = len(self.balancer.active_controllers)
            return jsonify({"status": "success", "count": count,"message": f"Deployed new controller. Total Active: {count}"})

        @self.app.route('/scale_down', methods=['POST'])
        def scale_down():
            self.balancer.logger.info("Manual Scale DOWN (-)")
            self.balancer.scale_down()
            count = len(self.balancer.active_controllers)
            return jsonify({"status": "success", "count": count,"message": f"Removed controller. Total Active: {count}"})

        @self.app.route('/init_balancer', methods=['POST'])
        def init_balancer():
            """Starts the monitoring and auto-scaling loop."""
            self.balancer.logger.info("Load Balancer Activated")
            # We trigger the OVS connections and start the monitoring thread
            self.balancer.update_ovs_connections()
            self.balancer.distribute_switches()
            
            # This flag would be checked in the 'run' method of the balancer
            self.balancer.monitoring_active = True
            self.balancer.auto_mode = True 
            return jsonify({"status": "success", "message": "Load Balancer is now active"})

        @self.app.route('/status', methods=['GET'])
        def get_status():
            """Returns real-time metrics for the charts and status indicators."""
            return jsonify({
                "active_controllers": sorted(list(self.balancer.active_controllers)),
                "avg_load": round(self.balancer.current_avg_load, 2),
                "individual_rates": self.balancer.current_rates,
                "is_scaling": (time.time() - self.balancer.last_scale_action_time) < self.balancer.COOLDOWN_TIME,
                "max_controllers": self.balancer.MAX_CONTROLLERS
            })

        @self.app.route('/generate_traffic', methods=['POST'])
        def generate_traffic():
            """Launches the traffic generator script on host m_p1."""
            
            data = request.get_json()
            if not data: data = {}
            pps = data.get('pps', 100)
            duration = data.get('time', 60)

            self.balancer.logger.info(f"Generating Traffic-> {pps} PPS for {duration}s")
            try:
                cmd = "sudo mn_exec m_p1 python3 traffic_gen.py 100 60 &"
                subprocess.Popen(cmd, shell=True)
                return jsonify({"status": "success", "message": "Traffic Injection Started: {pps} PPS for {duration}s on m_p1"})
            except Exception as e:
                self.balancer.logger.error(f"Traffic generation failed: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

        
    def run(self):
        """Launching Flask Server"""
        self.app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)