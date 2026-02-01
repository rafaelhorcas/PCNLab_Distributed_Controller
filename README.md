# PCN Lab Topic 3: Implementation of a Distributed Control Plane for Ryu

## Authors
* **Rafael Horcas Mateo** - email: [rafael.horcas@tum.de](mailto:rafael.horcas@tum.de)
* **Gorka Vila Pérez** - email: [gorka.vila@tum.de](mailto:gorka.vila@tum.de)

## Project Overview

This project implements an SDN-based Load Balancer capable of **auto-scaling** a cluster of Ryu controllers based on real-time network traffic. It leverages **Mininet** for network emulation, **Docker** for controller isolation, and a **Web GUI** for management and visualization.

## Features

* **Dynamic Auto-Scaling:** Automatically spawns or removes Dockerized Ryu controllers based on Traffic thresholds (PPS).
* **Load Balancing:** Distributes switches among active controllers using Round-Robin logic.
* **Real-Time Dashboard:** Web interface displaying live traffic metrics, network topology and system status.
* **Fault Tolerance:** Automatically redistributes switches if a controller fails or is removed.
* **Integrated Traffic Generator:** Tools to stress-test the network using UDP packet injection.

## Prerequisites

To run this project, you need a **Linux** environment (Ubuntu 20.04/22.04 recommended) with the following installed:

* **Python 3.8+**
* **Docker Engine**
* **Mininet** 
* **Open vSwitch**.

## How to Run

We have provided an automated script to set up the environment, dependencies, and launch the system.

1. **Open a terminal** in the project root folder.
2. **Execute the startup script:**

```bash
sudo ./start_demo.sh
```
What does this script do?

* Builds the ryu-controller Docker image
* Installs required Python dependencies
* Launches the Load Balancer
* Starts the Web GUI

Once the script is running, open your web browser and navigate to: http://localhost:5000

## Usage Guide

### 1. Initialize Network
Click the **"START MININET"** button. This will initialize the network topology (Switches and Hosts). The button will turn red to indicate the network is active.

### 2. Manage Controllers
* **First Controller:** Click **`+`** to add the first controller.
    * *Note:* The first controller automatically becomes **MASTER** and starts processing traffic immediately.
* **Manual Scaling:** You can manually add more controllers with **`+`**.
    * *Note:* Subsequent controllers will remain as **SLAVES** (standby) until the Load Balancer is activated.

### 3. Activate Auto-Scaling
Click **"START LOAD BALANCER"**.
* The system enters **Auto-Mode**.
* Switches are redistributed among all active controllers using Round-Robin.
* The system will automatically **Scale Up** (create containers) or **Scale Down** (remove containers) based on the traffic load (PPS).

### 4. Generate Traffic
Use the **"GENERATE TRAFFIC"** panel to inject UDP packets from host `h1`.
* **PPS:** Set the target Packets Per Second.
* **Time:** Set the duration of the test.
