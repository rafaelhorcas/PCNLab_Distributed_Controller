import pandas as pd
import matplotlib.pyplot as plt

def generate_test2_distribution():
    # Load the results
    try:
        df = pd.read_csv('../experiment_results.csv')
    except FileNotFoundError:
        print("Error: experiment_results.csv not found.")
        return

    # Data Cleaning: Handle the -1 artifact and outliers
    for i in range(5):
        col = f'ryu_{i}_pps'
        if col in df.columns:
            df.loc[df[col] < 0, col] = 0
            # Optional: handle the 7000 pps spike if it appears in this test too
            df.loc[df[col] > 1000, col] = 0 

    # Filter for the specific window (13s to 86s)

    plt.figure(figsize=(10, 6))

    # Plot lines for each controller that has at least some traffic
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
    for i in range(5):
        col = f'ryu_{i}_pps'
        if col in df.columns and df[col].max() > 0:
            # We use a solid line for Ryu 0 and dashed for others to distinguish
            linestyle = '-' if i == 0 else '--'
            plt.plot(df['elapsed_s'], df[col], 
                     label=f'Ryu {i}', color=colors[i], 
                     linewidth=2, linestyle=linestyle)

    # Chart Configuration (English)
    plt.title('Test 3: Failover Recovery Traffic Distribution', fontsize=16)
    plt.xlabel('Time (seconds)')
    plt.ylabel('Packets Per Second (PPS)')
    
    # Set the exact window
    plt.xlim(5, 130)
    
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Save the plot
    plt.savefig('test3_fail_recovery.png')
    print("Graph generated: test2_load_distribution.png")

if __name__ == "__main__":
    generate_test2_distribution()