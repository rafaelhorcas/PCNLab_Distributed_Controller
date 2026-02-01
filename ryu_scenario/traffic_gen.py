import socket
import random
import time
import sys

def generate_traffic(target_ip_prefix, rate_pps, duration):
    """
    Generates UDP traffic towards random IP addresses to force Packet-In messages.

    This function floods the network with UDP packets destined for random hosts
    within the specified subnet prefix.

    Args:
        target_ip_prefix (str): The subnet prefix for destination IPs
        rate_pps (int): Target transmission rate in Packets Per Second.
        duration (int): Total duration of the traffic generation in seconds.
    """
    print(f"--- Starting Generator: {rate_pps} PPS for {duration}s ---")
    
    # Create a simple UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    interval = 1.0 / rate_pps
    end_time = time.time() + duration
    packets_sent = 0

    try:
        while time.time() < end_time:
            start_loop = time.time()
            
            # Generate a random destination IP
            random_host = random.randint(1, 254)
            dst_ip = f"{target_ip_prefix}{random_host}"
            
            # Send a small packet (dummy payload)
            msg = b"TEST_PACKET"
            try:
                # Use a random port
                dst_port = random.randint(1024, 65000)
                sock.sendto(msg, (dst_ip, dst_port))
                packets_sent += 1
            except Exception as e:
                pass

            # Simple Rate Limiting logic
            elapsed = time.time() - start_loop
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        print("\nStopped by user.")
    
    print(f"--- Finished. Sent: {packets_sent} packets. ---")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 traffic_gen.py <PPS> <DURATION>")
        print("Example: python3 traffic_gen.py 60 20")
        sys.exit(1)
        sys.exit(1)
        
    pps = int(sys.argv[1])
    sec = int(sys.argv[2])
    
    # Assuming network 10.0.0.X
    generate_traffic("10.0.0.", pps, sec)