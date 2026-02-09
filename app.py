import streamlit as st
import pandas as pd
import pdfplumber
import re
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.distance import geodesic
import folium
from streamlit_folium import st_folium
from fpdf import FPDF
import urllib.parse
import time
import osmnx as ox
import matplotlib.pyplot as plt
import io

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Rutas Maestro", page_icon="🚛", layout="wide")

# --- ESTILOS CSS PARA BOTONES ---
st.markdown("""
<style>
    .big-button {width: 100%; padding: 20px; font-size: 20px; font-weight: bold; border-radius: 10px;}
    .reportview-container .main .block-container{padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- FUNCIONES ---

@st.cache_data
def extraer_datos_pdf(uploaded_file):
    """Extrae calles y números del PDF."""
    data = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    row = [str(cell).strip() if cell else "" for cell in row]
                    texto_fila = " ".join(row).lower()
                    if "calle" in texto_fila or "elemento" in texto_fila: continue
                    calle, numero = "", ""
                    for cell in row:
                        if len(cell) > 5 and not cell.isdigit():
                            calle = cell
                            break
                    for cell in row:
                        if cell != calle and (any(char.isdigit() for char in cell) or "Esq" in cell or "Frente" in cell):
                            numero = cell
                            break
                    if calle:
                        data.append({"calle": calle, "detalle": numero, "original": f"{calle} {numero}"})
            
            # Fallback texto plano
            if not data:
                text = page.extract_text()
                lines = text.split('\n')
                for line in lines:
                    if len(line) > 5: data.append({"calle": line, "detalle": "", "original": line})
    return pd.DataFrame(data)

def geolocalizar_puntos(df, api_delay):
    geolocator = Nominatim(user_agent="ruta_sevilla_app_v3")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=api_delay)
    coordenadas = []
    progreso = st.progress(0)
    total = len(df)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        query = f"{row['calle']}, {row['detalle']}, Sevilla, España"
        query_clean = query.replace("Frente", "").replace("Esq", "").replace("Prox", "")
        try:
            loc = geocode(query_clean)
            if not loc: loc = geocode(f"{row['calle']}, Sevilla, España")
            
            if loc: coordenadas.append((loc.latitude, loc.longitude, row['calle'], row['detalle']))
            else: coordenadas.append((None, None, row['calle'], row['detalle']))
        except:
            coordenadas.append((None, None, row['calle'], row['detalle']))
        
        progreso.progress((i + 1) / total)
        status_text.text(f"📍 Localizando {i+1}/{total}: {row['calle']}")
    
    status_text.empty()
    progreso.empty()
    return pd.DataFrame(coordenadas, columns=['lat', 'lon', 'calle', 'detalle'])

def optimizar_ruta(df_geo, inicio_str=None, fin_str=None):
    puntos = df_geo.dropna(subset=['lat', 'lon']).to_dict('records')
    no_encontrados = df_geo[df_geo['lat'].isna()]
    if not puntos: return None, no_encontrados

    ruta = []
    
    # Inicio
    geolocator = Nominatim(user_agent="ruta_start")
    actual = puntos[0]
    if inicio_str:
        try:
            loc = geolocator.geocode(f"{inicio_str}, Sevilla, España")
            if loc: actual = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[INICIO] {inicio_str}", 'detalle': ''}
        except: pass
    
    # Final
    final = None
    if fin_str:
        try:
            loc = geolocator.geocode(f"{fin_str}, Sevilla, España")
            if loc: final = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[FINAL] {fin_str}", 'detalle': ''}
        except: pass

    # Algoritmo Vecino más cercano
    ruta.append(actual)
    # Si usamos el primero de la lista como inicio, lo quitamos de pendientes
    if actual in puntos: puntos.remove(actual)
    
    pendientes = puntos
    
    while pendientes:
        mas_cercano = min(pendientes, key=lambda p: geodesic((actual['lat'], actual['lon']), (p['lat'], p['lon'])).meters)
        ruta.append(mas_cercano)
        pendientes.remove(mas_cercano)
        actual = mas_cercano
    
    if final: ruta.append(final)
    
    return pd.DataFrame(ruta), no_encontrados

def generar_html_gmaps(df):
    base_url = "https://www.google.com/maps/dir/"
    html = """<style>.btn {display:block;width:100%;padding:15px;background:#28a745;color:white;text-align:center;text-decoration:none;border-radius:8px;margin-bottom:10px;font-family:sans-serif;font-weight:bold;font-size:16px;}</style>"""
    chunk_size = 10
    registros = df.to_dict('records')
    for i in range(0, len(registros), chunk_size):
        chunk = registros[i:i+chunk_size]
        stops = "/".join([urllib.parse.quote(f"{r['calle']} {r['detalle']}, Sevilla") for r in chunk])
        link = f"{base_url}{stops}"
        html += f'<a href="{link}" target="_blank" class="btn">📍 TRAMO {i//chunk_size + 1} ({chunk[0]["calle"][:15]}...)</a>'
    return html

def generar_pdf(df):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Hoja de Ruta Optimizada", 0, 1, 'C')
    pdf.set_font("Arial", "", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(10, 8, "#", 1, 0, 'C', 1)
    pdf.cell(110, 8, "Calle", 1, 0, 'L', 1)
    pdf.cell(70, 8, "Detalle", 1, 1, 'L', 1)
    for i, row in df.iterrows():
        pdf.cell(10, 7, str(i+1), 1, 0, 'C')
        pdf.cell(110, 7, str(row['calle'])[:55], 1, 0, 'L')
        pdf.cell(70, 7, str(row['detalle'])[:35], 1, 1, 'L')
    return pdf.output(dest='S').encode('latin-1')

# --- ESTADO DE SESIÓN ---
if 'ruta_optimizada' not in st.session_state:
    st.session_state.ruta_optimizada = None

# --- INTERFAZ ---
st.title("🚛 Gestor de Rutas Maestro")

with st.sidebar:
    st.header("1. Cargar Datos")
    inicio_manual = st.text_input("📍 Inicio (Opcional)", placeholder="Ej: Base, Av. Blas Infante")
    fin_manual = st.text_input("🏁 Final (Opcional)", placeholder="Ej: Cartuja")
    archivo = st.file_uploader("📂 Subir PDF con Calles", type="pdf")
    
    st.divider()
    st.header("2. Configuración Mapa")
    distancia_mapa = st.slider("📏 Radio Mapa (m)", 500, 5000, 1500)
    mostrar_nombres = st.checkbox("Mostrar nombres calles", value=True)

if archivo:
    if st.button("🚀 PROCESAR Y OPTIMIZAR RUTA", type="primary", use_container_width=True):
        with st.spinner("Analizando PDF y calculando rutas..."):
            df_raw = extraer_datos_pdf(archivo)
            df_geo = geolocalizar_puntos(df_raw, 1.1)
            df_opt, df_err = optimizar_ruta(df_geo, inicio_manual, fin_manual)
            
            if df_opt is not None:
                st.session_state.ruta_optimizada = df_opt
                st.success(f"✅ Ruta optimizada con {len(df_opt)} paradas.")
            else:
                st.error("No se pudieron localizar direcciones.")

# --- RESULTADOS ---
if st.session_state.ruta_optimizada is not None:
    df = st.session_state.ruta_optimizada
    
    # 1. VISOR DE RUTA (TABLA)
    with st.expander("📄 Ver Listado de Ruta Ordenado", expanded=True):
        st.dataframe(df[['calle', 'detalle']], use_container_width=True)

    st.divider()

    # 2. SECCIÓN GPS HTML (REQUISITO 1: ANTES DEL MAPA VECTORIAL)
    st.subheader("📲 1. Enlaces GPS (Para Móvil)")
    col_gps1, col_gps2 = st.columns([1, 1])
    
    html_gps = generar_html_gmaps(df)
    
    with col_gps1:
        st.info("👇 **Descarga este archivo y envíalo al móvil:**")
        st.download_button("⬇️ Descargar Archivo HTML GPS", data=html_gps, file_name="Ruta_GPS_Botones.html", mime="text/html", use_container_width=True)
    
    with col_gps2:
        st.warning("👇 **O usa los enlaces directos ahora mismo:**")
        st.components.v1.html(html_gps, height=200, scrolling=True)

    st.divider()

    # 3. SECCIÓN MAPA VECTORIAL (REQUISITO 2: MAPA CON OPCIÓN DE LÍNEA)
    st.subheader("🗺️ 2. Mapa Vectorial (Para Imprimir)")
    
    col_map1, col_map2 = st.columns([1, 3])
    
    with col_map1:
        st.write("Configuración:")
        # Ubicación central automática basada en la ruta
        centro_lat = df['lat'].mean()
        centro_lon = df['lon'].mean()
        
        # OPCIÓN CLAVE: DIBUJAR RUTA
        dibujar_ruta = st.checkbox("🖊️ **Marcar línea de ruta (Inicio -> Fin)**", value=True, help="Dibuja el camino optimizado sobre el mapa limpio.")
        
        if st.button("🔄 Generar Mapa Limpio"):
            with st.spinner("Descargando cartografía de Sevilla..."):
                try:
                    # Descargar grafo OSM
                    G = ox.graph_from_point((centro_lat, centro_lon), dist=distancia_mapa, network_type='drive')
                    
                    # Graficar
                    fig, ax = ox.plot_graph(G, node_size=0, edge_color="#b0b0b0", edge_linewidth=0.5, 
                                            bgcolor="#FFFFFF", show=False, close=False, figsize=(10, 10))
                    
                    # A. Poner nombres calles (Opcional)
                    if mostrar_nombres:
                        for _, edge in ox.graph_to_gdfs(G, nodes=False).iterrows():
                            if 'name' in edge and isinstance(edge['name'], str) and edge.geometry.length > 150:
                                centroid = edge.geometry.centroid
                                ax.annotate(edge['name'], (centroid.x, centroid.y), fontsize=5, alpha=0.6, color='#555', ha='center')

                    # B. DIBUJAR LA RUTA (REQUISITO CLAVE)
                    if dibujar_ruta:
                        # Extraer lat/lon de la ruta optimizada
                        y = df['lat'].values
                        x = df['lon'].values
                        
                        # Dibujar línea roja conectando los puntos
                        ax.plot(x, y, color='red', linewidth=2, alpha=0.8, marker='o', markersize=3, label='Ruta')
                        
                        # Marcar Inicio (Verde) y Fin (Negro)
                        ax.plot(x[0], y[0], marker='*', color='green', markersize=15, label='Inicio')
                        ax.plot(x[-1], y[-1], marker='X', color='black', markersize=12, label='Fin')
                        
                        ax.legend(loc='upper right')

                    # Guardar en buffer
                    fn_pdf = io.BytesIO()
                    fig.savefig(fn_pdf, format='pdf', bbox_inches='tight')
                    fn_pdf.seek(0)
                    
                    # Mostrar en pantalla
                    with col_map2:
                        st.pyplot(fig)
                        st.download_button("⬇️ Descargar Mapa en PDF", data=fn_pdf, file_name="Mapa_Vectorial_Ruta.pdf", mime="application/pdf", use_container_width=True)

                except Exception as e:
                    st.error(f"Error generando mapa: {e}")
