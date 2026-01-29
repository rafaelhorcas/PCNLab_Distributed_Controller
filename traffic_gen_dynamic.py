import socket
import random
import time
import sys

def generate_dynamic_traffic(target_ip_prefix, initial_pps, duration):
    """
    Genera tráfico UDP dinámico aumentando 50 PPS cada 20 segundos.
    Mantiene IPs aleatorias para evitar el Flow Cache del switch.
    """
    print(f"--- Starting Dynamic Generator ---")
    print(f"Base Rate: {initial_pps} PPS | Increment: +50 PPS every 20s | Duration: {duration}s")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    start_time = time.time()
    end_time = start_time + duration
    packets_sent = 0
    
    # Parámetros de la "escalera"
    step_duration = 20  # segundos
    increment = 20      # PPS
    
    try:
        while time.time() < end_time:
            start_loop = time.time()
            elapsed = start_loop - start_time
            
            # --- CÁLCULO DINÁMICO DEL PPS ---
            # Cada 20s, subimos un escalón de 50 PPS
            current_step = int(elapsed // step_duration)
            current_pps = initial_pps + (current_step * increment)
            interval = 1.0 / current_pps
            
            # --- LÓGICA DE ENVÍO (Mantenemos tu random host) ---
            random_host = random.randint(1, 254)
            dst_ip = f"{target_ip_prefix}{random_host}"
            dst_port = random.randint(1024, 65000)
            
            msg = b"TEST_PACKET_FOR_SDN_SCALING"
            try:
                sock.sendto(msg, (dst_ip, dst_port))
                packets_sent += 1
            except Exception:
                pass

            # Control de logs cada 5 segundos para ver cómo sube
            if int(elapsed) % 5 == 0 and (elapsed - int(elapsed)) < interval:
                print(f"T: {int(elapsed)}s | Current Rate: {current_pps} PPS | Total Sent: {packets_sent}")

            # Rate Limiting ajustable en tiempo real
            loop_elapsed = time.time() - start_loop
            sleep_time = interval - loop_elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        print("\nStopped by user.")
    
    print(f"--- Finished. Total packets sent: {packets_sent} ---")

if __name__ == "__main__":
    # Uso: python3 dynamic_traffic_gen.py <BASE_PPS> <DURACION>
    if len(sys.argv) != 3:
        print("Usage: python3 dynamic_traffic_gen.py <INITIAL_PPS> <TOTAL_DURATION>")
        print("Example: python3 dynamic_traffic_gen.py 50 120")
        sys.exit(1)
        
    initial_pps = int(sys.argv[1])
    duration = int(sys.argv[2])
    
    generate_dynamic_traffic("10.0.0.", initial_pps, duration)