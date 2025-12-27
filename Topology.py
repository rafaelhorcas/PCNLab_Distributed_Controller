
from mininet.topo import Topo

class ProjectTopology(Topo):
    "Simplified TUM network topology"

    def build(self):

        # Adding hosts
        m_p1 = self.addHost('m_p1', ip='10.0.0.1/24', mac='08:00:00:00:00:01')               
        m_p2 = self.addHost('m_p2', ip='10.0.0.2/24', mac='08:00:00:00:00:02')               
        m_s1 = self.addHost('m_s1', ip='10.0.0.3/24', mac='08:00:00:00:00:03')               
        m_s2 = self.addHost('m_s2', ip='10.0.0.4/24', mac='08:00:00:00:00:04')               
        g_p1 = self.addHost('g_p1', ip='10.0.0.5/24', mac='08:00:00:00:00:05')               
        g_p2 = self.addHost('g_p2', ip='10.0.0.6/24', mac='08:00:00:00:00:06')               
        g_s1 = self.addHost('g_s1', ip='10.0.0.7/24', mac='08:00:00:00:00:07')               
        g_s2 = self.addHost('g_s2', ip='10.0.0.8/24', mac='08:00:00:00:00:08')
        
        # Adding switches
        munich = self.addSwitch('s1')                  
        munich_prof = self.addSwitch('s2')             
        munich_stud = self.addSwitch('s3')             
        garching = self.addSwitch('s4')                
        garching_prof = self.addSwitch('s5')           
        garching_stud = self.addSwitch('s6')
             
        # Adding links   
        self.addLink(munich,garching)                      
        self.addLink(garching,garching_prof)                     
        self.addLink(garching,garching_stud)                      
        self.addLink(munich,munich_prof)                     
        self.addLink(munich,munich_stud)                    
        self.addLink(m_p1,munich_prof)                      
        self.addLink(m_p2,munich_prof)                       
        self.addLink(m_s1,munich_stud)                      
        self.addLink(m_s2,munich_stud)                   
        self.addLink(g_p1,garching_prof)                     
        self.addLink(g_p2,garching_prof)                      
        self.addLink(g_s1,garching_stud)
        self.addLink(g_s2,garching_stud)
        
topos = { 'mytopo': ( lambda: ProjectTopology() ) }