import socket
import struct
import random
import time
import sys

def generate_traffic(target_ip_prefix, rate_pps, duration):
    """
    Genera tráfico UDP hacia IPs aleatorias para forzar Packet-Ins.
    target_ip_prefix: "10.0.0."
    rate_pps: Paquetes por segundo
    duration: Segundos que durará el ataque
    """
    print(f"--- Iniciando Generador: {rate_pps} PPS durante {duration}s ---")
    
    # Creamos un socket UDP simple
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    interval = 1.0 / rate_pps
    end_time = time.time() + duration
    packets_sent = 0

    try:
        while time.time() < end_time:
            start_loop = time.time()
            
            # Generamos una IP destino aleatoria para evitar el Flow Cache
            # Ej: 10.0.0.55, 10.0.0.123...
            random_host = random.randint(1, 254)
            dst_ip = f"{target_ip_prefix}{random_host}"
            
            # Enviamos un paquete pequeño (mensaje basura)
            msg = b"TEST_PACKET_FOR_SDN_SCALING"
            try:
                # Puerto aleatorio también por si acaso
                dst_port = random.randint(1024, 65000)
                sock.sendto(msg, (dst_ip, dst_port))
                packets_sent += 1
            except Exception as e:
                pass

            # Control de velocidad (Rate Limiting) simple
            elapsed = time.time() - start_loop
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        print("\nDetenido por usuario.")
    
    print(f"--- Fin. Enviados: {packets_sent} paquetes. ---")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python3 traffic_gen.py <PPS> <DURACION>")
        print("Ejemplo: python3 traffic_gen.py 60 20")
        sys.exit(1)
        
    pps = int(sys.argv[1])
    sec = int(sys.argv[2])
    
    # Asumimos red 10.0.0.X
    generate_traffic("10.0.0.", pps, sec)