import os
import re
import matplotlib.pyplot as plt

def generate_dependency_graph(root_dir):
    print(f"Scanning HealthAI directory: {root_dir}")
    
    # Updated to include all 28 modules found in your project structure
    modules = [
        'agents', 'analytics', 'appeals', 'assembly', 'audit', 'cases', 
        'evidence', 'evidence_ai', 'explainability', 'extraction', 'feedback', 
        'governance', 'guidelines', 'ingestion', 'metrics', 'models', 'ocr', 
        'operations', 'payers', 'quality', 'resolution', 'review', 
        'storage', 'tabs', 'tests', 'ui', 'validation', 'vision'
    ]
    connections = []

    # Scan codebase for imports
    for root, _, files in os.walk(os.path.join(root_dir, 'app')):
        for file in files:
            if file.endswith('.py'):
                current_mod = os.path.basename(root)
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        for mod in modules:
                            if mod != current_mod and re.search(r'(from app\.' + mod + r'|import ' + mod + r')', content):
                                connections.append((current_mod, mod))
                except Exception:
                    pass

    connections = list(set(connections))
    
    if not connections:
        print("No internal app dependencies detected.")
        return

    # Filter to unique modules that actually have relationships
    unique_modules = sorted(list(set([node for edge in connections for node in edge])))
    
    # Initialize a clean matrix grid plot
    fig, ax = plt.subplots(figsize=(12, 11))
    
    # Plot a distinct data dot for every active import connection
    for edge in connections:
        importer = edge[0]
        dependency = edge[1]
        
        y_idx = unique_modules.index(importer)
        x_idx = unique_modules.index(dependency)
        
        # Draw a solid point at the intersection grid coordinate
        ax.scatter(x_idx, y_idx, color='teal', s=120, edgecolors='darkslategray', zorder=3)
    
    # Grid & Axis Configurations
    ax.set_xticks(range(len(unique_modules)))
    ax.set_xticklabels(unique_modules, rotation=45, ha='right', fontsize=10)
    ax.set_yticks(range(len(unique_modules)))
    ax.set_yticklabels(unique_modules, fontsize=10)
    
    ax.set_xlabel("Is Imported By This Module (The Dependency)", fontsize=12, fontweight='bold', labelpad=15)
    ax.set_ylabel("This Module Imports It... (The Source)", fontsize=12, fontweight='bold', labelpad=15)
    ax.set_title("HealthAI Dependency Matrix Map", fontsize=16, fontweight='bold', pad=20)
    
    # Create background grid lines for easy alignment scanning
    ax.grid(True, which='both', color='gainsboro', linestyle='-', linewidth=0.7, zorder=1)
    
    # Invert the Y-axis so labels read cleanly from A to Z top-to-bottom
    ax.invert_yaxis()
    
    plt.tight_layout()
    output_path = os.path.join(root_dir, 'graphify', 'architecture_graph.png')
    plt.savefig(output_path, dpi=150)
    print(f"Success! Clean dependency matrix saved to: {output_path}")

if __name__ == "__main__":
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    generate_dependency_graph(project_root)