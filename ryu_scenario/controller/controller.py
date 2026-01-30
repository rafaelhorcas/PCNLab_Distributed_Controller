from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib.packet import packet, ethernet, arp, ipv4, ether_types
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event as topo_event
from ryu.app.wsgi import WSGIApplication
import networkx as nx
from controllerRESTAPI import RestAPI
import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class Controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(Controller, self).__init__(*args, **kwargs)
        
        # REST API
        wsgi = kwargs['wsgi']
        wsgi.register(RestAPI, {'controller_app': self})
        
        # Role Management
        self.switches_roles = {}
        
        # Load Balancing Metrics
        self.packet_in_count = 0
        self.datapaths = {}
    
        # Creating the network graph
        self.network = nx.DiGraph()

        self.MONITOR_PERIOD = 30
        self.monitor_thread = hub.spawn(self.monitor)
        
        # Adding the switches and links
        self.SWITCH_TYPE = "switch"
        self.HOST_TYPE = "host"

        # Utils
        self.DEFAULT_TABLE = 0
        self.LOW_PRIORITY = 0
        self.MEDIUM_PRIORITY = 50
        self.HIGH_PRIORITY = 100
        self.IDLE_TIMEOUT = 60
        self.HARD_TIMEOUT = 60
        self.IP_ICMP = 0X01 # Byte PROTOCOL in IP header
        self.IP_TCP = 0x06  # Byte PROTOCOL in IP header
        self.DEFAULT_HOST_PORT = 1
        
    # FUNCTIONS TO ADD ELEMENTS TO THE NETWORK
    
    # Function to handle switch enter event
    @set_ev_cls(topo_event.EventSwitchEnter, MAIN_DISPATCHER)
    def new_switch_handler(self, ev):
        switch = ev.switch
        dp = switch.dp
        dpid = dp.id
        self.logger.info(f"Switch s{dpid} detected")
        self.datapaths[dpid] = dp
        # Add the switch (node) to the network graph
        self.network.add_node(dpid, type=self.SWITCH_TYPE, name=f"s{dpid}", dp=dp, tx_pkts=0, num_flows=0)

        # Default Rule - Table Miss (send to controller)
        ofproto = dp.ofproto
        parser = dp.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,ofproto.OFPCML_NO_BUFFER)] 
        self.add_flow(dp=dp, table=self.DEFAULT_TABLE, priority=self.LOW_PRIORITY, match=match, actions=actions)
        
    # Function to handle link enter event
    @set_ev_cls(topo_event.EventLinkAdd, MAIN_DISPATCHER)
    def new_link_handler(self, ev):
        link = ev.link
        src = link.src.dpid
        src_port = link.src.port_no
        dst = link.dst.dpid
        dst_port = link.dst.port_no
        self.logger.info(f"Link s{src} <--> s{dst} detected")

        # Adding the switches and links
        self.network.add_edge(src, dst, src_port=src_port, dst_port=dst_port)
        self.network.add_edge(dst, src, src_port=dst_port, dst_port=src_port)

    # Function to handle host add event
    @set_ev_cls(topo_event.EventHostAdd, MAIN_DISPATCHER)
    def new_host_handler(self, ev):
        host = ev.host
        host_ipv4 = host.ipv4[0]
        host_mac = host.mac
        dpid = host.port.dpid
        dpid_port = host.port.port_no
        self.logger.info(f"Host {host_mac} ({host_ipv4}) detected")

        # Adding the host and its links to the switch
        self.network.add_node(host_ipv4, type=self.HOST_TYPE, mac=host_mac)
        self.network.add_edge(host_ipv4, dpid, src_port=self.DEFAULT_HOST_PORT, dst_port=dpid_port)
        self.network.add_edge(dpid, host_ipv4, src_port=dpid_port, dst_port=self.DEFAULT_HOST_PORT)

    def monitor(self):
        while True:
            self.logger.info("Printing topology information")
            for node1, node2, data in self.network.edges(data=True):
                node1_str = str(node1)
                node2_str = str(node2)
                # If the name includes a dot, it is an IP address, thus a host
                if '.' in node1_str:
                    node1_str = f"h{node1_str}"
                else:
                    node1_str = f"s{node1_str}"
                if '.' in node2_str:
                    node2_str = f"h{node2_str}"
                else:
                    node2_str = f"s{node2_str}"
                self.logger.info(f"{node1_str}-eth{data['src_port']} --> {node2_str}-eth{data['dst_port']}")
            hub.sleep(self.MONITOR_PERIOD)

    def add_flow(self, dp, table, priority, match, actions=None, buffer_id=None, i_tout=0, h_tout=0):
        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id:
            mod = parser.OFPFlowMod(datapath=dp, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst, table_id=table,
                                    idle_timeout=i_tout, hard_timeout=h_tout)
        else:
            mod = parser.OFPFlowMod(datapath=dp, priority=priority,
                                    match=match, instructions=inst, table_id=table,
                                    idle_timeout=i_tout, hard_timeout=h_tout)

        dp.send_msg(mod)

    # Function to handle packet in events
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        # Extracts switch info
        dp = ev.msg.datapath
        datapath = ev.msg.datapath
        dpid = dp.id
        in_port = ev.msg.match['in_port']
        # Extracts OF handlers
        ofproto = dp.ofproto
        parser = dp.ofproto_parser
        # Extracts the packet
        pkt_in = packet.Packet(ev.msg.data)
        eth_header = pkt_in.get_protocols(ethernet.ethernet)[0]
        dst_mac = eth_header.dst
        src_mac = eth_header.src
        ethertype = eth_header.ethertype

        # LLDP packets are ignored
        if ethertype == ether_types.ETH_TYPE_LLDP:
            return

        current_role = self.switches_roles.get(dpid, 'EQUAL')
        if current_role == 'SLAVE':
            return

        self.packet_in_count += 1
        self.logger.info(f"PacketIn s{dpid}: {src_mac} → {dst_mac}")
        
        if ethertype == ether_types.ETH_TYPE_ARP:
            arp_header = pkt_in.get_protocols(arp.arp)[0]
            src_ip = arp_header.src_ip
            dst_ip = arp_header.dst_ip        
        elif ethertype == ether_types.ETH_TYPE_IP:
            ip_header = pkt_in.get_protocols(ipv4.ipv4)[0]
            src_ip = ip_header.src
            dst_ip = ip_header.dst
        else:
            return
        # Default output port is FLOOD
        out_port = datapath.ofproto.OFPP_FLOOD
        
        # If the destination is known in the graph, try to find the shortest path
        if self.network.has_node(dpid) and self.network.has_node(dst_ip):
            try:
                shortest_path = nx.shortest_path(self.network, source=dpid, target=dst_ip)
                if len(shortest_path) >= 2:
                    next_hop = shortest_path[1]
                    out_port = self.network[dpid][next_hop]['src_port']
            except (nx.NetworkXNoPath, KeyError):
                out_port = datapath.ofproto.OFPP_FLOOD
        
        actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow if the destination IP is known
        if out_port != datapath.ofproto.OFPP_FLOOD:
            if ethertype == ether_types.ETH_TYPE_ARP:
                match = parser.OFPMatch(eth_type=ethertype, arp_spa=src_ip, arp_tpa=dst_ip)
            else:
                match = parser.OFPMatch(eth_type=ethertype, ipv4_src=src_ip, ipv4_dst=dst_ip)
            
            self.add_flow(dp=datapath, table=self.DEFAULT_TABLE, priority=self.HIGH_PRIORITY, 
                        match=match, actions=actions, i_tout=10)

        # Send the packet out
        data = None
        if ev.msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER:
            data = ev.msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=ev.msg.buffer_id,
                                in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        dpid = datapath.id

        if ev.state == DEAD_DISPATCHER:
            if dpid in self.datapaths: del self.datapaths[dpid]
            return

        self.datapaths[dpid] = datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # A. SET ASYNC MESSAGES
        packet_in_mask = [
            ofproto.OFPR_NO_MATCH | ofproto.OFPR_ACTION | ofproto.OFPR_INVALID_TTL,
            ofproto.OFPR_NO_MATCH | ofproto.OFPR_ACTION | ofproto.OFPR_INVALID_TTL
        ]
        port_status_mask = [
             ofproto.OFPPR_ADD | ofproto.OFPPR_DELETE | ofproto.OFPPR_MODIFY,
             ofproto.OFPPR_ADD | ofproto.OFPPR_DELETE | ofproto.OFPPR_MODIFY
        ]
        req_async = parser.OFPSetAsync(datapath, packet_in_mask, port_status_mask, [0,0])
        datapath.send_msg(req_async)
        
        # B. DEFAULT ROLE: EQUAL
        self.switches_roles[dpid] = "SLAVE"

    def set_role(self, dpid, role_str, gen_id):
        if dpid not in self.datapaths: return False
        
        dp = self.datapaths[dpid]
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        self.switches_roles[dpid] = role_str.upper()
        
        role_of = ofp.OFPCR_ROLE_MASTER if role_str.upper() == 'MASTER' else ofp.OFPCR_ROLE_SLAVE
        req = parser.OFPRoleRequest(dp, role=role_of, generation_id=gen_id)
        dp.send_msg(req)
        
        self.logger.info(f"Role updated for s{dpid}: {role_str}")
        return True
