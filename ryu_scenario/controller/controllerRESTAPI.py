import os
import sys
from ryu.app.wsgi import ControllerBase, Response, route
from ryu.lib import dpid as dpid_lib
import json
import networkx as nx
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from BaseLogger import BaseLogger

class RestAPI(ControllerBase, BaseLogger):

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data['controller_app']
        BaseLogger.__init__(self, log_name="api_rest", log_level="DEBUG")

    @route('metrics', '/metrics', methods=['GET'])
    def get_metrics(self, req, **kwargs):
        body = {
            'packet_in_count': self.app.packet_in_count,
            'switches': list(self.app.datapaths.keys())
        }
        return Response(
            content_type='application/json',
            body=json.dumps(body)
        )

    @route('role', '/role', methods=['POST'])
    def set_role(self, req, **kwargs):
        data = json.loads(req.body)

        dpid = int(data['dpid'])
        role_str = data['role']
        gen_id = int(data.get('generation_id', 0))
        
        if dpid not in self.app.datapaths:
            return Response(status=404)

        success = self.app.set_role(dpid, role_str, gen_id)

        if success:
            return Response(status=200, body=f"Role updated to {role_str}")
        else:
            return Response(status=404, body="Switch not found")
        

    @route('roles', '/roles', methods=['GET'])
    def get_roles(self, req, **kwargs):
        connected_dpids = list(self.app.datapaths.keys())
        roles_map = self.app.switches_roles.copy()
        body = {
            'controller_id': id(self.app),
            'packet_in_count': self.app.packet_in_count,
            'switches_connected': connected_dpids,
            'roles_table': roles_map
        }
        
        return Response(
            content_type='application/json',
            body=json.dumps(body)
        )

