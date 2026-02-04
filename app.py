import sys
!{sys.executable} -m pip install streamlit osmnx matplotlib geopy scikit-learn

import streamlit as st
import osmnx as ox
import matplotlib.pyplot as plt
import io

# Configuración de la página
st.set_page_config(page_title="Generador de Mapas de Ruta", page_icon="🗺️", layout="wide")

st.title("🗺️ Generador de Mapas Limpios para Rutas")
st.markdown("Crea mapas minimalistas (solo calles) listos para imprimir y dibujar tu ruta.")

# --- BARRA LATERAL (CONTROLES) ---
st.sidebar.header("⚙️ Configuración")

# 1. Entrada de ubicación
ubicacion = st.sidebar.text_input("📍 Ubicación Central:", "Constitucion, Sevilla, Spain")
distancia = st.sidebar.slider("📏 Radio (metros):", 500, 5000, 1000, step=100)

# 2. Opciones de Estilo
st.sidebar.subheader("Estilo")
mostrar_nombres = st.sidebar.checkbox("Mostrar nombres de calles", value=True)
titulo_mapa = st.sidebar.text_input("Título del Mapa (Opcional):", "")

# 3. Botón de Generar
generar = st.sidebar.button("🚀 Generar Mapa", type="primary")

# --- ÁREA PRINCIPAL ---

if generar:
    with st.spinner(f'Descargando datos de "{ubicacion}" y generando vectores...'):
        try:
            # Lógica de OSMNX (Igual que en Colab)
            G = ox.graph_from_address(ubicacion, dist=distancia, network_type='drive')
            G = ox.project_graph(G)

            # Crear Figura
            fig, ax = ox.plot_graph(
                G,
                node_size=0,
                edge_color="#000000",
                edge_linewidth=1.0,
                bgcolor="#FFFFFF",
                show=False,
                close=False,
                figsize=(12, 12)
            )

            # Añadir Nombres
            if mostrar_nombres:
                seen_names = set()
                for _, edge in ox.graph_to_gdfs(G, nodes=False).iterrows():
                    if 'name' in edge and isinstance(edge['name'], str) and edge.geometry.length > 200:
                        name = edge['name']
                        if name not in seen_names:
                            centroid = edge.geometry.centroid
                            ax.annotate(name, (centroid.x, centroid.y),
                                        fontsize=6, alpha=0.8, color='darkred', ha='center')
                            seen_names.add(name)

            # Añadir Título
            if titulo_mapa:
                ax.text(0.5, 1.02, titulo_mapa, transform=ax.transAxes,
                        fontsize=18, fontweight='bold', ha='center', color='black')

            # Mostrar en pantalla
            st.pyplot(fig)

            # --- SECCIÓN DE DESCARGA ---
            st.success("¡Mapa generado!")

            # Preparar PDF en memoria
            fn_pdf = io.BytesIO()
            fig.savefig(fn_pdf, format='pdf', bbox_inches='tight', facecolor='white')
            fn_pdf.seek(0)

            # Preparar PNG en memoria
            fn_png = io.BytesIO()
            fig.savefig(fn_png, format='png', dpi=300, bbox_inches='tight', facecolor='white')
            fn_png.seek(0)

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="⬇️ Descargar PDF (Vectorial)",
                    data=fn_pdf,
                    file_name="mapa_ruta.pdf",
                    mime="application/pdf"
                )
            with col2:
                st.download_button(
                    label="⬇️ Descargar PNG (Imagen)",
                    data=fn_png,
                    file_name="mapa_ruta.png",
                    mime="image/png"
                )

        except Exception as e:
            st.error(f"Error: No se encontró la dirección o hubo un problema de red. ({e})")
else:
    # Mensaje de bienvenida / Instrucciones
    st.info("👈 Usa el menú de la izquierda para configurar tu zona y pulsa 'Generar Mapa'.")
    # Mapa base simple solo para referencia visual (usando st.map no interactivo o folium si se quisiera complejo)
    st.map(latitude=[40.416], longitude=[-3.703], zoom=12) # Mapa dummy inicial