from ryu.app.wsgi import ControllerBase, Response, route
from ryu.lib import dpid as dpid_lib
import json
import networkx as nx

class RestAPI(ControllerBase):

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data['controller_app']

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

        dp = self.app.datapaths[dpid]
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        if role_str == 'MASTER':
            role = ofp.OFPCR_ROLE_MASTER
            self.app.IS_MASTER = True
        elif role_str == 'SLAVE':
            role = ofp.OFPCR_ROLE_SLAVE
            self.app.IS_MASTER = False
        else:
            return Response(status=400)

        req_role = parser.OFPRoleRequest(
            dp,
            role=role,
            generation_id=gen_id
        )
        dp.send_msg(req_role)

        return Response(status=200)


