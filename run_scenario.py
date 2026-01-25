from time import sleep
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import OVSSwitch, Host, RemoteController

from Topology import ProjectTopology

class Runner:

    def run_scenario(self):
        # Initialize mininet with the topology specified by the config
        self.create_network()
        self.net.start()
        sleep(1)

        self.do_net_cli()
        # stop right after the CLI is exited
        self.net.stop()

    def create_network(self):
        print("Building mininet topology.")
        self.topo = ProjectTopology()
        self.net = Mininet(topo=self.topo, link=TCLink, host=Host, switch=OVSSwitch, controller=None)

    def do_net_cli(self):
        print("Starting mininet CLI")
        print('')
        print('======================================================================')
        print('Welcome to Mininet CLI!')
        print('======================================================================')
        print('You can interact with the network using the mininet CLI below.')
        print('')
        CLI(self.net)


if __name__ == '__main__':
    exercise = Runner()
    exercise.run_scenario()
