
import streamlit as st
import osmnx as ox
import matplotlib.pyplot as plt
import io

st.set_page_config(page_title="Generador de Mapas de Ruta", page_icon="🗺️", layout="wide")

st.title("🗺️ Generador de Mapas Limpios para Rutas")
st.markdown("Crea mapas minimalistas listos para imprimir.")

# --- BARRA LATERAL ---
st.sidebar.header("⚙️ Configuración")
ubicacion = st.sidebar.text_input("📍 Ubicación Central:", "Sevilla, Spain")
distancia = st.sidebar.slider("📏 Radio (metros):", 500, 5000, 1000, step=100)
mostrar_nombres = st.sidebar.checkbox("Mostrar nombres de calles", value=True)
titulo_mapa = st.sidebar.text_input("Título del Mapa:", "")
generar = st.sidebar.button("🚀 Generar Mapa", type="primary")

# --- ÁREA PRINCIPAL ---
if generar:
    with st.spinner(f'Descargando datos de "{ubicacion}"...'):
        try:
            G = ox.graph_from_address(ubicacion, dist=distancia, network_type='drive')
            G = ox.project_graph(G)
            
            fig, ax = ox.plot_graph(G, node_size=0, edge_color="#000000", edge_linewidth=1.0, 
                                    bgcolor="#FFFFFF", show=False, close=False, figsize=(12, 12))
            
            if mostrar_nombres:
                seen_names = set()
                for _, edge in ox.graph_to_gdfs(G, nodes=False).iterrows():
                    if 'name' in edge and isinstance(edge['name'], str) and edge.geometry.length > 200:
                        name = edge['name']
                        if name not in seen_names:
                            centroid = edge.geometry.centroid
                            ax.annotate(name, (centroid.x, centroid.y), fontsize=6, alpha=0.8, color='darkred', ha='center')
                            seen_names.add(name)
            
            if titulo_mapa:
                ax.text(0.5, 1.02, titulo_mapa, transform=ax.transAxes, fontsize=18, fontweight='bold', ha='center', color='black')

            st.pyplot(fig)
            
            # Botones de descarga
            fn_pdf = io.BytesIO()
            fig.savefig(fn_pdf, format='pdf', bbox_inches='tight', facecolor='white')
            fn_pdf.seek(0)
            st.download_button("⬇️ Descargar PDF", data=fn_pdf, file_name="mapa_ruta.pdf", mime="application/pdf")
            
        except Exception as e:
            st.error(f"Error: {e}")
else:
    st.info("Configura la zona en el menú de la izquierda.")
