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
from matplotlib.backends.backend_pdf import PdfPages
import io
import networkx as nx

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Rutas Maestro", page_icon="🚛", layout="wide")

# --- ESTILOS CSS ---
st.markdown("""
<style>
    .big-button {width: 100%; padding: 20px; font-size: 20px; font-weight: bold; border-radius: 10px;}
    div.stButton > button:first-child {background-color: #28a745; color: white; border-radius: 8px;}
</style>
""", unsafe_allow_html=True)

# --- 1. FUNCIONES DE LECTURA Y GEOLOCALIZACIÓN ---

@st.cache_data
def extraer_datos_pdf(uploaded_file):
    """Lee el PDF y extrae calles y números."""
    data = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    # Limpieza y conversión a string
                    row = [str(cell).strip() if cell else "" for cell in row]
                    texto_fila = " ".join(row).lower()
                    
                    # Saltar cabeceras comunes
                    if "calle" in texto_fila or "elemento" in texto_fila or "descripción" in texto_fila: 
                        continue
                    
                    calle, numero = "", ""
                    
                    # Lógica heurística para detectar calle vs número
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
            
            # Fallback a texto plano si no hay tablas
            if not data:
                text = page.extract_text()
                if text:
                    lines = text.split('\n')
                    for line in lines:
                        if len(line) > 5: data.append({"calle": line, "detalle": "", "original": line})
    return pd.DataFrame(data)

def geolocalizar_puntos(df, api_delay):
    """Convierte direcciones en coordenadas Lat/Lon."""
    geolocator = Nominatim(user_agent="ruta_sevilla_app_master_clean")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=api_delay)
    
    coordenadas = []
    progreso = st.progress(0)
    total = len(df)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        # Construir query. Importante: Añadir ciudad y país
        query = f"{row['calle']}, {row['detalle']}, Sevilla, España"
        query_clean = query.replace("Frente", "").replace("Esq", "").replace("Prox", "").replace("(", "").replace(")", "")
        
        try:
            loc = geocode(query_clean)
            # Si falla con el número, intentar solo con la calle
            if not loc: 
                loc = geocode(f"{row['calle']}, Sevilla, España")
            
            if loc: 
                coordenadas.append((loc.latitude, loc.longitude, row['calle'], row['detalle']))
            else: 
                coordenadas.append((None, None, row['calle'], row['detalle']))
        except:
            coordenadas.append((None, None, row['calle'], row['detalle']))
        
        progreso.progress((i + 1) / total)
        status_text.text(f"📍 Localizando {i+1}/{total}: {row['calle']}")
    
    status_text.empty()
    progreso.empty()
    return pd.DataFrame(coordenadas, columns=['lat', 'lon', 'calle', 'detalle'])

# --- 2. FUNCIÓN DE OPTIMIZACIÓN (VECINO MÁS CERCANO) ---

def optimizar_ruta(df_geo, inicio_str=None, fin_str=None):
    puntos = df_geo.dropna(subset=['lat', 'lon']).to_dict('records')
    no_encontrados = df_geo[df_geo['lat'].isna()]
    
    if not puntos: return None, no_encontrados

    ruta = []
    geolocator = Nominatim(user_agent="ruta_start_end")
    
    # Gestionar Punto de Inicio
    actual = puntos[0]
    if inicio_str:
        try:
            loc = geolocator.geocode(f"{inicio_str}, Sevilla, España")
            if loc: actual = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[INICIO] {inicio_str}", 'detalle': ''}
        except: pass
    
    # Gestionar Punto Final
    final = None
    if fin_str:
        try:
            loc = geolocator.geocode(f"{fin_str}, Sevilla, España")
            if loc: final = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[FINAL] {fin_str}", 'detalle': ''}
        except: pass

    # Algoritmo
    ruta.append(actual)
    if actual in puntos: puntos.remove(actual) # Evitar duplicar si el inicio estaba en la lista
    
    pendientes = puntos
    
    while pendientes:
        # Busca el punto pendiente más cercano al punto 'actual'
        mas_cercano = min(pendientes, key=lambda p: geodesic((actual['lat'], actual['lon']), (p['lat'], p['lon'])).meters)
        ruta.append(mas_cercano)
        pendientes.remove(mas_cercano)
        actual = mas_cercano
    
    if final: ruta.append(final)
    
    return pd.DataFrame(ruta), no_encontrados

# --- 3. FUNCIONES DE GENERACIÓN DE DOCUMENTOS ---

def generar_html_gmaps(df):
    base_url = "https://www.google.com/maps/dir/"
    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"><title>Ruta Digital</title>
    <style>
        body{font-family:sans-serif;padding:20px;background:#f4f4f9}
        .btn{display:block;width:100%;padding:15px;background:#28a745;color:white;text-align:center;text-decoration:none;border-radius:8px;margin-bottom:10px;font-weight:bold;font-size:16px;box-shadow:0 2px 5px rgba(0,0,0,0.2)}
        .container{background:white;padding:20px;border-radius:10px;margin-top:20px;box-shadow:0 2px 5px rgba(0,0,0,0.1)}
        table{width:100%;border-collapse:collapse;margin-top:10px}
        th,td{border-bottom:1px solid #ddd;padding:12px;text-align:left}
        th{background:#f2f2f2}
        tr:nth-child(even){background:#f9f9f9}
        h2{color:#333;border-bottom:2px solid #28a745;padding-bottom:10px}
    </style>
    </head><body>
    <h2>📲 1. Navegación GPS (Tramos)</h2>"""
    
    chunk_size = 10
    registros = df.to_dict('records')
    for i in range(0, len(registros), chunk_size):
        chunk = registros[i:i+chunk_size]
        stops = "/".join([urllib.parse.quote(f"{r['calle']} {r['detalle']}, Sevilla") for r in chunk])
        link = f"{base_url}{stops}"
        html += f'<a href="{link}" target="_blank" class="btn">📍 ABRIR TRAMO {i//chunk_size + 1}</a>'

    html += """<div class="container"><h2>📋 2. Listado Ordenado</h2><table><thead><tr><th>#</th><th>Calle</th><th>Detalle</th></tr></thead><tbody>"""
    for i, row in df.iterrows():
        html += f"<tr><td><b>{i+1}</b></td><td>{row['calle']}</td><td>{row['detalle']}</td></tr>"
    html += "</tbody></table></div></body></html>"
    return html

def generar_pdf_listado(df):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Hoja de Ruta Optimizada", 0, 1, 'C') # Prefijo eliminado
    pdf.ln(5)
    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(220, 220, 220)
    pdf.cell(10, 8, "#", 1, 0, 'C', 1)
    pdf.cell(110, 8, "Calle", 1, 0, 'L', 1)
    pdf.cell(70, 8, "Detalle", 1, 1, 'L', 1)
    pdf.set_font("Arial", "", 10)
    for i, row in df.iterrows():
        calle = str(row['calle']).encode('latin-1', 'replace').decode('latin-1')[:55]
        detalle = str(row['detalle']).encode('latin-1', 'replace').decode('latin-1')[:35]
        pdf.cell(10, 7, str(i+1), 1, 0, 'C')
        pdf.cell(110, 7, calle, 1, 0, 'L')
        pdf.cell(70, 7, detalle, 1, 1, 'L')
    return pdf.output(dest='S').encode('latin-1')

# --- 4. FUNCIÓN GENERADORA DE ATLAS (MULTIPAGE MAP) ---

def crear_mapa_atlas(G, df, mostrar_nombres=True):
    pdf_buffer = io.BytesIO()
    
    # Límites
    lats = df['lat'].values
    lons = df['lon'].values
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2
    
    margin = 0.0015
    
    # 5 Vistas: General + 4 Cuadrantes
    views = [
        ("VISTA GENERAL (Ruta Completa)", (min_lon-margin, max_lon+margin), (min_lat-margin, max_lat+margin)),
        ("ZOOM 1: Noroeste", (min_lon-margin, mid_lon+margin/2), (mid_lat-margin/2, max_lat+margin)),
        ("ZOOM 2: Noreste", (mid_lon-margin/2, max_lon+margin), (mid_lat-margin/2, max_lat+margin)),
        ("ZOOM 3: Suroeste", (min_lon-margin, mid_lon+margin/2), (min_lat-margin, mid_lat+margin/2)),
        ("ZOOM 4: Sureste", (mid_lon-margin/2, max_lon+margin), (min_lat-margin, mid_lat+margin/2))
    ]

    with PdfPages(pdf_buffer) as pdf:
        for title, xlim, ylim in views:
            # Crear figura A4 Landscape
            fig, ax = ox.plot_graph(G, node_size=0, edge_color="#999999", edge_linewidth=0.5, 
                                    bgcolor="#FFFFFF", show=False, close=False, figsize=(11.7, 8.3))
            
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
            
            # Dibujar ruta
            ax.plot(lons, lats, color='red', linewidth=3, alpha=0.7, marker='o', markersize=4, label='Ruta')
            
            # Marcar inicio/fin si son visibles
            if xlim[0] < lons[0] < xlim[1] and ylim[0] < lats[0] < ylim[1]:
                ax.plot(lons[0], lats[0], marker='*', color='green', markersize=20, markeredgecolor='black', label='INICIO')
            if xlim[0] < lons[-1] < xlim[1] and ylim[0] < lats[-1] < ylim[1]:
                ax.plot(lons[-1], lats[-1], marker='X', color='black', markersize=15, markeredgecolor='white', label='FIN')

            ax.set_title(title, fontsize=18, fontweight='bold', pad=10)
            
            # Etiquetas de calles inteligentes (solo las visibles en el zoom actual)
            if mostrar_nombres:
                seen_names = set()
                for _, edge in ox.graph_to_gdfs(G, nodes=False).iterrows():
                    if 'name' in edge and isinstance(edge['name'], str):
                        geom = edge.geometry
                        centroid = geom.centroid
                        if xlim[0] < centroid.x < xlim[1] and ylim[0] < centroid.y < ylim[1]:
                            if geom.length > 80: # Filtro por longitud de calle
                                name = edge['name']
                                if name not in seen_names:
                                    ax.annotate(name, (centroid.x, centroid.y), fontsize=7, 
                                                alpha=0.9, color='#000066', ha='center', fontweight='bold',
                                                bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=1))
                                    seen_names.add(name)

            pdf.savefig(fig, bbox_inches='tight')
            plt.close(fig)
            
    pdf_buffer.seek(0)
    return pdf_buffer

# --- INTERFAZ DE USUARIO (SIDEBAR) ---
st.sidebar.title("🚛 Gestor de Rutas")
st.sidebar.markdown("---")
st.sidebar.header("1. Carga de Datos")
inicio_manual = st.sidebar.text_input("📍 Inicio (Opcional)", placeholder="Ej: Av. Blas Infante")
fin_manual = st.sidebar.text_input("🏁 Final (Opcional)", placeholder="Ej: Cartuja")
archivo = st.sidebar.file_uploader("📂 Subir PDF", type="pdf")
api_delay = st.sidebar.slider("Velocidad API (Segundos)", 1.0, 3.0, 1.1)

st.sidebar.markdown("---")
st.sidebar.header("2. Opciones de Mapa")
mostrar_nombres = st.sidebar.checkbox("Nombres de calles", value=True)

# --- ESTADO DE SESIÓN ---
if 'ruta_optimizada' not in st.session_state:
    st.session_state.ruta_optimizada = None

# --- LÓGICA PRINCIPAL ---
st.title("🚛 Gestor de Rutas Maestro")

if archivo:
    if st.button("🚀 PROCESAR Y OPTIMIZAR RUTA", type="primary", use_container_width=True):
        with st.spinner("Leyendo PDF, geolocalizando puntos y calculando ruta óptima..."):
            df_raw = extraer_datos_pdf(archivo)
            df_geo = geolocalizar_puntos(df_raw, api_delay)
            df_opt, df_err = optimizar_ruta(df_geo, inicio_manual, fin_manual)
            
            if df_opt is not None:
                st.session_state.ruta_optimizada = df_opt
                st.success(f"✅ ¡Ruta calculada con éxito! ({len(df_opt)} puntos)")
            else:
                st.error("No se pudieron geolocalizar direcciones suficientes.")

# --- RESULTADOS Y DESCARGAS ---
if st.session_state.ruta_optimizada is not None:
    df = st.session_state.ruta_optimizada
    
    # TABS PARA ORGANIZAR LA VISTA
    tab1, tab2, tab3 = st.tabs(["📄 Listado & GPS", "🗺️ Mapa Interactivo", "🖨️ Impresión (Atlas)"])
    
    with tab1:
        st.subheader("1. Descargas Digitales")
        col1, col2 = st.columns(2)
        with col1:
            st.info("Para el Móvil (Botones GPS + Listado)")
            html_gps = generar_html_gmaps(df)
            st.download_button("⬇️ HTML Interactivo", data=html_gps, file_name="Ruta_Digital_GPS.html", mime="text/html", use_container_width=True)
        with col2:
            st.info("Para Imprimir (Solo Listado)")
            pdf_bytes = generar_pdf_listado(df)
            st.download_button("⬇️ PDF Listado Limpio", data=pdf_bytes, file_name="Listado_Calles.pdf", mime="application/pdf", use_container_width=True)
        
        with st.expander("Ver Tabla de Datos"):
            st.dataframe(df[['calle', 'detalle']], use_container_width=True)

    with tab2:
        st.subheader("2. Visualización Rápida")
        try:
            m = folium.Map(location=[df.iloc[0]['lat'], df.iloc[0]['lon']], zoom_start=13)
            points = df[['lat', 'lon']].values.tolist()
            folium.PolyLine(points, color="blue", weight=3, opacity=0.8).add_to(m)
            for i, row in df.iterrows():
                color = "green" if i==0 else "black" if i==len(df)-1 else "blue"
                folium.Marker([row['lat'], row['lon']], popup=f"{i+1}. {row['calle']}", icon=folium.Icon(color=color, icon="truck", prefix="fa")).add_to(m)
            st_folium(m, width=800, height=500)
        except:
            st.warning("Mapa interactivo no disponible.")

    with tab3:
        st.subheader("3. Generar Atlas Vectorial (PDF Multipage)")
        st.write("Crea un PDF con **5 páginas**: 1 General + 4 Zooms detallados con nombres de calles.")
        
        if st.button("🔄 Generar Atlas PDF"):
            with st.spinner("Descargando mapa de calles y generando 5 páginas... (Paciencia)"):
                try:
                    # Descargar grafo con un margen seguro
                    lats, lons = df['lat'].values, df['lon'].values
                    north, south = max(lats)+0.005, min(lats)-0.005
                    east, west = max(lons)+0.005, min(lons)-0.005
                    
                    G = ox.graph_from_bbox(north, south, east, west, network_type='drive')
                    
                    pdf_atlas = crear_mapa_atlas(G, df, mostrar_nombres)
                    
                    st.success("✅ Atlas generado correctamente.")
                    st.download_button("⬇️ Descargar Atlas (5 Páginas)", data=pdf_atlas, file_name="Atlas_Ruta.pdf", mime="application/pdf", use_container_width=True)
                except Exception as e:
                    st.error(f"Error generando el mapa: {e}")
