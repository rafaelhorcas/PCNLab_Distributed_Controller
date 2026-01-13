import requests
import time

# --- CONFIGURATION ---
# Controller REST endpoints
CONTROLLERS = {
    "c1": {"url": "http://localhost:8081", "role": "SLAVE"},
    "c2": {"url": "http://localhost:8082", "role": "SLAVE"}
}

# Load difference threshold to avoid frequent leader changes
LOAD_THRESHOLD = 10

# Global generation ID (must be monotonically increasing for OVS)
CURRENT_GEN_ID = 0


def get_metrics():
    """
    Query each controller for load metrics.
    Expected response:
      {
        "packet_in_count": int,
        "switches": [dpid, ...]
      }
    """
    data = {}
    for name, info in CONTROLLERS.items():
        try:
            r = requests.get(f"{info['url']}/metrics", timeout=2).json()
            data[name] = r
            print(f"[{name}] OK - Packet-In count: {r['packet_in_count']}")
        except requests.exceptions.RequestException:
            print(f"[{name}] DOWN - No response")
            data[name] = None
    return data


def set_master_controller(master_name, switches_list):
    """
    Assign MASTER / SLAVE roles to controllers for each switch.
    Uses an incremental generation_id as required by OpenFlow/OVS.
    """
    global CURRENT_GEN_ID
    CURRENT_GEN_ID += 1

    print(f"\nLeader change initiated: new MASTER = {master_name} (generation_id={CURRENT_GEN_ID})")

    for sw_id in switches_list:
        for name, info in CONTROLLERS.items():
            new_role = "MASTER" if name == master_name else "SLAVE"

            payload = {
                "dpid": sw_id,
                "role": new_role,
                "generation_id": CURRENT_GEN_ID
            }

            try:
                response = requests.post(
                    f"{info['url']}/role",
                    json=payload,
                    timeout=2
                )

                if response.status_code == 200:
                    print(f"Switch {sw_id}: {name} set to {new_role}")
                else:
                    print(f"Switch {sw_id}: {name} role update failed (HTTP {response.status_code})")

            except requests.exceptions.RequestException as e:
                print(f"Switch {sw_id}: error contacting {name} ({e})")


def run_balancer():
    print("Starting SDN controller load balancer")
    current_master = None

    while True:
        print("\nChecking controller metrics...")
        metrics = get_metrics()

        # Require all controllers to be reachable
        if metrics["c1"] is None or metrics["c2"] is None:
            print("At least one controller is unreachable. Retrying...")
            time.sleep(5)
            continue

        load_c1 = metrics["c1"]["packet_in_count"]
        load_c2 = metrics["c2"]["packet_in_count"]

        proposed_master = current_master

        # Initial leader selection
        if current_master is None:
            proposed_master = "c1" if load_c1 <= load_c2 else "c2"

        # Leader reelection based on load difference
        else:
            diff = abs(load_c1 - load_c2)
            if diff > LOAD_THRESHOLD:
                proposed_master = "c1" if load_c1 < load_c2 else "c2"
            else:
                print(f"Load difference ({diff}) below threshold. Keeping current leader.")

        # Apply leader change if needed
        if proposed_master != current_master:
            switches = metrics["c1"]["switches"] or metrics["c2"]["switches"]

            if not switches:
                print("No switches detected. Waiting...")
            else:
                set_master_controller(proposed_master, switches)
                current_master = proposed_master
        else:
            print(f"Current leader remains unchanged: {current_master}")

        time.sleep(5)


if __name__ == "__main__":
    run_balancer()
