from ryu.app.wsgi import ControllerBase, Response, route
import json

class RestAPI(ControllerBase):
    """
    REST API controller for the Ryu application.
    Exposes endpoints for monitoring metrics, managing switch roles, and retrieving topology data.
    """

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data['controller_app']

    @route('metrics', '/metrics', methods=['GET'])
    def get_metrics(self, req, **kwargs):
        """
        Retrieves the current performance metrics of the controller.

        Args:
            req: The HTTP request object.

        Returns:
            Response: A JSON response containing:
                - packet_in_count (int): Total number of Packet-In messages processed.
                - switches (list): List of datapath IDs (DPIDs) currently connected.
        """
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
        """
        Updates the OpenFlow role (MASTER/SLAVE) for a specific switch.

        Expects a JSON body with:
            - dpid (int): The Datapath ID of the switch.
            - role (str): The desired role ('MASTER' or 'SLAVE').
            - generation_id (int, optional): The generation ID for the role request.

        Args:
            req: The HTTP request object containing the JSON body.

        Returns:
            Response: 
                - 200 OK: If the role was successfully updated.
                - 404 Not Found: If the switch is not connected.
        """
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
        """
        Retrieves the current role status for all connected switches.

        Args:
            req: The HTTP request object.

        Returns:
            Response: A JSON response containing:
                - controller_id (int): Memory address ID of the controller instance.
                - packet_in_count (int): Total Packet-In messages processed.
                - switches_connected (list): List of connected DPIDs.
                - roles_table (dict): Mapping of DPID to current Role (MASTER/SLAVE).
        """
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
        
    @route('topology', '/topology', methods=['GET'])
    def get_topology(self, req, **kwargs):
        """
        Extracts the network topology graph for visualization purposes.
        
        Converts the internal NetworkX graph into a JSON format suitable for 
        frontend libraries like Vis.js.

        Args:
            req: The HTTP request object.

        Returns:
            Response: A JSON response containing 'nodes' and 'edges' lists.
        """
        # Extract the nodes
        nodes = []
        for node_id, data in self.app.network.nodes(data=True):
            nodes.append({
                'id': node_id,
                'label': data.get('name', str(node_id)),
                'group': data.get('type', 'switch')
            })

        # Extract the edges
        edges = []
        for src, dst in self.app.network.edges():
            edges.append({
                'from': src,
                'to': dst
            })

        body = {
            'nodes': nodes,
            'edges': edges
        }

        return Response(
            content_type='application/json',
            headers={'Access-Control-Allow-Origin': '*'},
            body=json.dumps(body)
        )

