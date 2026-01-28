#!/bin/bash
cleanup() {
    sudo pkill -f "ryu_scenario/load_balancer/load_balancer.py"
    sudo pkill -f "ryu_scenario/run_scenario.py"
    sudo mn -c
    exit
}
trap cleanup SIGINT
sudo mn -c
sudo docker build -t ryu-controller .
sudo pip install -r requirements.txt
sudo rm -f *.log
sudo python3 ryu_scenario/load_balancer/load_balancer.py &
sleep 3
python3 -m http.server 8000 --directory gui