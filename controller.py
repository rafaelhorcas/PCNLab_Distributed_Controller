from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib.packet import packet, ethernet, arp, ipv4, tcp, ether_types
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event as topo_event
from ryu.app.wsgi import WSGIApplication, ControllerBase, route
import os
import networkx as nx
from controllerRESTAPI import RestAPI

class Controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(Controller, self).__init__(*args, **kwargs)
        
        # REST API
        wsgi = kwargs['wsgi']
        wsgi.register(RestAPI, {'controller_app': self})
        
        # Role Management
        role = os.environ.get("RYU_ROLE", "slave").lower()
        self.IS_MASTER = (role == "master")

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
        if not self.IS_MASTER:
            return
        
        self.packet_in_count += 1
        # Extracts switch info
        dp = ev.msg.datapath
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
        
        self.logger.info(f"PacketIn s{dpid}: {src_mac} → {dst_mac}")
        
        if ethertype == ether_types.ETH_TYPE_ARP:
            arp_header = pkt_in.get_protocols(arp.arp)[0]
            self.arp_handler(in_port=in_port, dp=dp, parser=parser, proto=ethertype, arp_header=arp_header, ev=ev)
            return
        
        if ethertype == ether_types.ETH_TYPE_IP:
            ip_header = pkt_in.get_protocols(ipv4.ipv4)[0]
            self.ip_handler(dp=dp, parser=parser,ip_header=ip_header, ev=ev)
            return
            
    def arp_handler(self, in_port, dp, parser,  proto, arp_header, ev):
        src_ip = arp_header.src_ip
        dst_ip = arp_header.dst_ip

        shortest_path = self.path_handler(src_ip, dst_ip)
        self.logger.info(f"Shortest path for ARP h{src_ip} --> h{dst_ip}: {shortest_path}")
        
        if shortest_path:
            for index, link in enumerate(shortest_path):
                src_sw, dst_sw = link
                hop_dp = self.network.nodes[src_sw]['dp']
                match = parser.OFPMatch(
                        eth_type=proto,
                        arp_spa=src_ip,
                        arp_tpa=dst_ip
                    )
                out_port = self.network.get_edge_data(src_sw, dst_sw)["src_port"]
                actions = [parser.OFPActionOutput(out_port)]
                self.logger.info(f"DP: {hop_dp.id}, Match: [ARP tpa: {dst_ip}], Out port: {out_port}")
                self.add_flow(dp=hop_dp, table=self.DEFAULT_TABLE, priority=self.HIGH_PRIORITY, match=match, actions=actions)
                
            # Forwarding initial ARP packet
            if index == len(shortest_path) - 1:
                in_port = self.network.get_edge_data(src_sw, dst_sw)["dst_port"]
                self.logger.info(f"Pkt-out DP: {hop_dp.id}, in-port: {in_port}, out-port: {out_port}")
                out = parser.OFPPacketOut(datapath=hop_dp, buffer_id=ev.msg.buffer_id, in_port=in_port, actions=actions, data=ev.msg.data)
                hop_dp.send_msg(out)
        else:
            actions = [parser.OFPActionOutput(dp.ofproto.OFPP_FLOOD)]
            out = parser.OFPPacketOut(
                datapath=dp,
                buffer_id=ev.msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=ev.msg.data
            )
            dp.send_msg(out)
            return

    def ip_handler(self, dp, parser, ip_header, ev):
        src_ip = ip_header.src
        dst_ip = ip_header.dst

        shortest_path = self.path_handler(src_ip, dst_ip)
        self.logger.info(f"Shortest path for IP h{src_ip} --> h{dst_ip}: {shortest_path}")
        
        if shortest_path:
            for index, link in enumerate(shortest_path):
                src_sw, dst_sw = link
                hop_dp = self.network.nodes[src_sw]['dp']
                match = parser.OFPMatch(
                        eth_type=ether_types.ETH_TYPE_IP,
                        ipv4_src=src_ip,
                        ipv4_dst=dst_ip
                )
                out_port = self.network.get_edge_data(src_sw, dst_sw)["src_port"]
                actions = [parser.OFPActionOutput(out_port)]
                self.logger.info(f"DP: {hop_dp.id}, Match: [src IP: {src_ip} dst IP: {dst_ip}], Out port: {out_port}")
                self.add_flow(dp=hop_dp, table=self.DEFAULT_TABLE, priority=self.HIGH_PRIORITY, match=match, actions=actions)
                
            # Forwarding initial IP packet
            if index == len(shortest_path) - 1:
                in_port = self.network.get_edge_data(src_sw, dst_sw)["dst_port"]
                self.logger.info(f"Pkt-out DP: {hop_dp.id}, in-port: {in_port}, out-port: {out_port}")
                out = parser.OFPPacketOut(datapath=hop_dp, buffer_id=ev.msg.buffer_id, in_port=in_port, actions=actions, data=ev.msg.data)
                hop_dp.send_msg(out)
        else:
            self.logger.warning(f"IP Handler: No path found for {src_ip} -> {dst_ip}. Dropping packet.")
            return

    def path_handler(self, src_ip, dst_ip):
        if self.network.has_node(src_ip) and self.network.has_node(dst_ip):
            try:
                shortest_path = nx.shortest_path(self.network, source=src_ip, target=dst_ip)
                shortest_path = list(zip(shortest_path[1:-1], shortest_path[2:]))
                self.logger.info(f"SP {src_ip} --> {dst_ip}: {shortest_path}")
                return shortest_path
            except nx.NetworkXNoPath:
                return []
        else:
            self.logger.info(f"No SP found between {src_ip} --> {dst_ip}")
            return []
    
    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)
    def state_change_handler(self, ev):
        datapath = ev.datapath
        dpid = datapath.id
        self.datapaths[datapath.id] = datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if self.IS_MASTER:
            role = ofproto.OFPCR_ROLE_MASTER
            role_str = "MASTER"
        else:
            role = ofproto.OFPCR_ROLE_SLAVE
            role_str = "SLAVE"

        req = parser.OFPRoleRequest(
            datapath,
            role=role,
            generation_id=0
        )
        datapath.send_msg(req)

        self.logger.info("Sent role %s to switch %s", role_str, dpid)
