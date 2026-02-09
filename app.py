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
st.set_page_config(page_title="Gestor de Rutas - Sevilla", page_icon="🚛", layout="wide")

# --- FUNCIONES DEL OPTIMIZADOR (Script 1) ---

@st.cache_data
def extraer_datos_pdf(uploaded_file):
    """Extrae calles y números del PDF usando pdfplumber."""
    data = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            # Intento 1: Extracción de tablas
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    row = [str(cell).strip() if cell else "" for cell in row]
                    texto_fila = " ".join(row).lower()
                    
                    if "calle" in texto_fila or "elemento" in texto_fila: 
                        continue 
                    
                    calle = ""
                    numero = ""
                    
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
            
            # Intento 2: Extracción de texto bruto
            if not data:
                text = page.extract_text()
                lines = text.split('\n')
                for line in lines:
                    if len(line) > 5:
                        data.append({"calle": line, "detalle": "", "original": line})
                        
    return pd.DataFrame(data)

def geolocalizar_puntos(df, api_delay):
    """Obtiene latitud y longitud para cada dirección."""
    geolocator = Nominatim(user_agent="ruta_sevilla_optimizer_app_v2")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=api_delay)
    
    coordenadas = []
    progreso = st.progress(0)
    total = len(df)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        query = f"{row['calle']}, {row['detalle']}, Sevilla, España"
        query_clean = query.replace("Frente", "").replace("Esq", "").replace("Prox", "")
        
        try:
            location = geocode(query_clean)
            if location:
                coordenadas.append((location.latitude, location.longitude, row['calle'], row['detalle']))
            else:
                location = geocode(f"{row['calle']}, Sevilla, España")
                if location:
                    coordenadas.append((location.latitude, location.longitude, row['calle'], row['detalle']))
                else:
                    coordenadas.append((None, None, row['calle'], row['detalle']))
        except:
            coordenadas.append((None, None, row['calle'], row['detalle']))
            
        progreso.progress((i + 1) / total)
        status_text.text(f"Geolocalizando {i+1}/{total}: {row['calle']}")
        
    return pd.DataFrame(coordenadas, columns=['lat', 'lon', 'calle', 'detalle'])

def optimizar_ruta_vecino_cercano(df_geo, inicio_str=None, fin_str=None):
    """Ordena los puntos usando el algoritmo del vecino más cercano."""
    puntos_validos = df_geo.dropna(subset=['lat', 'lon']).copy()
    no_encontrados = df_geo[df_geo['lat'].isna()]
    
    if puntos_validos.empty:
        return None, no_encontrados

    ruta_ordenada = []
    puntos_pendientes = puntos_validos.to_dict('records')
    
    punto_actual = None
    
    if inicio_str:
        geolocator = Nominatim(user_agent="ruta_sevilla_start")
        try:
            loc = geolocator.geocode(f"{inicio_str}, Sevilla, España")
            if loc:
                punto_actual = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[INICIO] {inicio_str}", 'detalle': ''}
            else:
                st.warning(f"⚠️ No se encontró el inicio: {inicio_str}. Se usará el primero de la lista.")
        except:
            pass
    
    if not punto_actual:
        punto_actual = puntos_pendientes.pop(0)
        ruta_ordenada.append(punto_actual)
    else:
        ruta_ordenada.append(punto_actual)
        
    punto_final = None
    if fin_str:
        geolocator = Nominatim(user_agent="ruta_sevilla_end")
        try:
            loc = geolocator.geocode(f"{fin_str}, Sevilla, España")
            if loc:
                punto_final = {'lat': loc.latitude, 'lon': loc.longitude, 'calle': f"[FINAL] {fin_str}", 'detalle': ''}
        except:
            pass

    while puntos_pendientes:
        mas_cercano = None
        distancia_minima = float('inf')
        indice_mas_cercano = -1
        
        for i, punto in enumerate(puntos_pendientes):
            dist = geodesic((punto_actual['lat'], punto_actual['lon']), (punto['lat'], punto['lon'])).meters
            if dist < distancia_minima:
                distancia_minima = dist
                mas_cercano = punto
                indice_mas_cercano = i
        
        punto_actual = puntos_pendientes.pop(indice_mas_cercano)
        ruta_ordenada.append(punto_actual)
        
    if punto_final:
        ruta_ordenada.append(punto_final)
        
    return pd.DataFrame(ruta_ordenada), no_encontrados

def generar_html_gmaps(df_ordenado):
    base_url = "https://www.google.com/maps/dir/"
    html = """
    <style>
        .btn {display:block;width:100%;padding:10px;background:#28a745;color:white;text-align:center;text-decoration:none;border-radius:5px;margin-bottom:10px;font-family:sans-serif;font-weight:bold;}
        .card {background:#f8f9fa;padding:15px;border-radius:10px;margin-bottom:15px;border:1px solid #ddd;}
    </style>
    <h3>🗺️ Enlaces de Navegación GPS</h3>
    """
    
    chunk_size = 10
    registros = df_ordenado.to_dict('records')
    
    for i in range(0, len(registros), chunk_size):
        chunk = registros[i:i+chunk_size]
        stops = "/".join([urllib.parse.quote(f"{r['calle']} {r['detalle']}, Sevilla") for r in chunk])
        link = f"{base_url}{stops}"
        
        html += f'<div class="card"><b>Tramo {i//chunk_size + 1}</b><br>'
        html += f'<span style="font-size:0.8em">{chunk[0]["calle"]} ➔ {chunk[-1]["calle"]}</span>'
        html += f'<a href="{link}" target="_blank" class="btn">📍 ABRIR EN MAPS</a></div>'
        
    return html

def generar_pdf(df_ordenado):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Hoja de Ruta Optimizada", 0, 1, 'C')
    pdf.ln(5)
    
    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(220, 220, 220)
    pdf.cell(10, 8, "#", 1, 0, 'C', 1)
    pdf.cell(100, 8, "Calle", 1, 0, 'L', 1)
    pdf.cell(80, 8, "Detalle", 1, 1, 'L', 1)
    
    pdf.set_font("Arial", "", 10)
    for i, row in df_ordenado.iterrows():
        calle_txt = str(row['calle'])[:50]
        detalle_txt = str(row['detalle'])[:40]
        pdf.cell(10, 7, str(i+1), 1, 0, 'C')
        pdf.cell(100, 7, calle_txt, 1, 0, 'L')
        pdf.cell(80, 7, detalle_txt, 1, 1, 'L')
        
    return pdf.output(dest='S').encode('latin-1')

# --- NAVEGACIÓN Y ESTRUCTURA PRINCIPAL ---

st.sidebar.title("Navegación")
modo = st.sidebar.radio("Selecciona una herramienta:", ["🚛 Optimizador de Ruta (PDF)", "🗺️ Generador de Mapa Vectorial"])

# ==========================================
# MODO 1: OPTIMIZADOR DE RUTA
# ==========================================
if modo == "🚛 Optimizador de Ruta (PDF)":
    st.title("🚛 Optimizador de Rutas - Sevilla")
    st.markdown("Sube tu listado en PDF, geolocaliza las calles y ordénalas automáticamente.")
    
    st.sidebar.header("⚙️ Configuración Ruta")
    inicio_manual = st.sidebar.text_input("📍 Punto de Inicio (Opcional)", placeholder="Ej: Base, Av. Blas Infante...")
    fin_manual = st.sidebar.text_input("🏁 Punto Final (Opcional)", placeholder="Ej: Cartuja, Vertedero...")
    api_delay = st.sidebar.slider("Velocidad API (seg)", 1.0, 3.0, 1.1)

    uploaded_file = st.file_uploader("📂 Adjunta tu listado de calles (PDF)", type="pdf")

    if uploaded_file is not None:
        st.info("Leyendo archivo... Por favor espera.")
        df_raw = extraer_datos_pdf(uploaded_file)
        
        st.write(f"✅ Se han detectado **{len(df_raw)}** puntos de recogida.")
        with st.expander("Ver listado detectado (Original)"):
            st.dataframe(df_raw)
            
        if st.button("🚀 OPTIMIZAR RUTA AHORA", type="primary"):
            st.write("---")
            st.write("🔄 **Paso 1: Geolocalizando direcciones...**")
            df_geo = geolocalizar_puntos(df_raw, api_delay)
            
            st.write("🔄 **Paso 2: Calculando ruta más rápida...**")
            df_optimo, df_error = optimizar_ruta_vecino_cercano(df_geo, inicio_manual, fin_manual)
            
            if df_optimo is not None:
                st.success("✅ ¡Ruta calculada con éxito!")
                
                # MAPA VISUAL
                st.subheader("🗺️ Vista Previa")
                try:
                    m = folium.Map(location=[df_optimo.iloc[0]['lat'], df_optimo.iloc[0]['lon']], zoom_start=13)
                    points = df_optimo[['lat', 'lon']].values.tolist()
                    folium.PolyLine(points, color="blue", weight=2.5, opacity=0.8).add_to(m)
                    
                    for i, row in df_optimo.iterrows():
                        icon_color = "green" if i == 0 else "red" if i == len(df_optimo)-1 else "blue"
                        folium.Marker(
                            [row['lat'], row['lon']], 
                            popup=f"{i+1}. {row['calle']}",
                            icon=folium.Icon(color=icon_color, icon="truck", prefix="fa")
                        ).add_to(m)
                    st_folium(m, width=700, height=500)
                except:
                    st.warning("No se pudo generar la vista previa del mapa interactivo.")

                # DESCARGAS
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("📄 Listado PDF")
                    pdf_bytes = generar_pdf(df_optimo)
                    st.download_button("Descargar PDF Ordenado", data=pdf_bytes, file_name="Ruta_Optimizada.pdf", mime="application/pdf")
                    
                with col2:
                    st.subheader("📱 Enlaces GPS")
                    html_code = generar_html_gmaps(df_optimo)
                    st.components.v1.html(html_code, height=400, scrolling=True)
                    st.download_button("Descargar HTML GPS", data=html_code, file_name="Ruta_GPS.html", mime="text/html")

                if not df_error.empty:
                    st.warning(f"⚠️ Atención: No se pudieron localizar {len(df_error)} calles.")
                    st.dataframe(df_error[['calle', 'detalle']])
            else:
                st.error("Error al calcular la ruta. Verifica las direcciones.")

# ==========================================
# MODO 2: GENERADOR DE MAPA VECTORIAL
# ==========================================
elif modo == "🗺️ Generador de Mapa Vectorial":
    st.title("🗺️ Generador de Mapas Limpios")
    st.markdown("Crea un mapa minimalista (estilo arquitecto) de una zona específica para imprimir.")
    
    st.sidebar.header("⚙️ Configuración Mapa")
    ubicacion = st.sidebar.text_input("📍 Ubicación Central:", "Sevilla, Spain")
    distancia = st.sidebar.slider("📏 Radio del mapa (metros):", 500, 5000, 1000, step=100)
    mostrar_nombres = st.sidebar.checkbox("Mostrar nombres de calles", value=True)
    titulo_mapa = st.sidebar.text_input("Título del Mapa (Opcional):", "Mapa de Sector")
    
    if st.button("🚀 Generar Mapa Vectorial", type="primary"):
        with st.spinner(f'Descargando datos de "{ubicacion}" y generando gráficos... (Esto puede tardar unos segundos)'):
            try:
                # Descargar grafo de OSM
                G = ox.graph_from_address(ubicacion, dist=distancia, network_type='drive')
                G = ox.project_graph(G)
                
                # Graficar
                fig, ax = ox.plot_graph(G, node_size=0, edge_color="#2c3e50", edge_linewidth=0.8, 
                                        bgcolor="#FFFFFF", show=False, close=False, figsize=(12, 12))
                
                # Añadir nombres de calles (opcional)
                if mostrar_nombres:
                    seen_names = set()
                    for _, edge in ox.graph_to_gdfs(G, nodes=False).iterrows():
                        if 'name' in edge and isinstance(edge['name'], str) and edge.geometry.length > 200:
                            name = edge['name']
                            if name not in seen_names:
                                centroid = edge.geometry.centroid
                                ax.annotate(name, (centroid.x, centroid.y), fontsize=5, alpha=0.7, color='darkred', ha='center')
                                seen_names.add(name)
                
                if titulo_mapa:
                    ax.text(0.5, 1.02, titulo_mapa, transform=ax.transAxes, fontsize=18, fontweight='bold', ha='center', color='black')

                st.pyplot(fig)
                
                # Botón de descarga PDF
                fn_pdf = io.BytesIO()
                fig.savefig(fn_pdf, format='pdf', bbox_inches='tight', facecolor='white')
                fn_pdf.seek(0)
                st.download_button("⬇️ Descargar Mapa en PDF", data=fn_pdf, file_name="mapa_sector_vectorial.pdf", mime="application/pdf")
                
            except Exception as e:
                st.error(f"Error al generar el mapa: {e}")
                st.info("Prueba a escribir la ubicación de otra forma (Ej: 'Triana, Sevilla, Spain')")
