# streamlit_app.py
import io
import re
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode

st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide")
st.title("Buscar CUIT en movimientos bancarios")

VERSION = "5.0"

# ---------- inicializar session_state ----------
if 'uploaded_personas_bytes' not in st.session_state:
    st.session_state['uploaded_personas_bytes'] = None
if 'uploaded_banco_bytes' not in st.session_state:
    st.session_state['uploaded_banco_bytes'] = None
if 'uploaded_personas_name' not in st.session_state:
    st.session_state['uploaded_personas_name'] = ''
if 'uploaded_banco_name' not in st.session_state:
    st.session_state['uploaded_banco_name'] = ''
if 'df_detalle_display' not in st.session_state:
    st.session_state['df_detalle_display'] = None
if 'res_sorted' not in st.session_state:
    st.session_state['res_sorted'] = None
if 'processed' not in st.session_state:
    st.session_state['processed'] = False
if 'search_lote' not in st.session_state:
    st.session_state['search_lote'] = ''

# ---------- utilidades ----------
def find_col(df, keywords):
    for k in keywords:
        for c in df.columns:
            if k.lower() in str(c).lower():
                return c
    return None

def only_digits(s):
    return re.sub(r'\D', '', str(s))

@st.cache_data
def read_excel_bytes_from_buffer(buf_bytes, ext_hint=None):
    buf = io.BytesIO(buf_bytes)
    try:
        if ext_hint and ext_hint.lower() == "xls":
            return pd.read_excel(buf, dtype=str).fillna('')
        else:
            return pd.read_excel(buf, dtype=str, engine="openpyxl").fillna('')
    except Exception:
        buf.seek(0)
        return pd.read_excel(buf, dtype=str).fillna('')

@st.cache_data
def process_files(personas_bytes, banco_bytes, personas_name, banco_name):
    """Procesa los bytes y devuelve (df_detalle_display, res_sorted). Cacheado."""
    personas = read_excel_bytes_from_buffer(personas_bytes, ext_hint=(personas_name.split('.')[-1] if personas_name else None))
    banco = read_excel_bytes_from_buffer(banco_bytes, ext_hint=(banco_name.split('.')[-1] if banco_name else None))

    # detectar columnas
    concepto_col = find_col(banco, ['concepto', 'concept'])
    credito_col = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto'])
    fecha_col = find_col(banco, ['fecha', 'date', 'fecha de'])
    if not concepto_col:
        raise ValueError("No se encontró la columna 'Concepto' en el archivo bancario.")
    cuit_col = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col = find_col(personas, ['lote'])
    golf_col = find_col(personas, ['golf'])
    if not cuit_col:
        raise ValueError("No se encontró la columna 'Cuit/Cuil' en el archivo de personas.")

    # preparar columnas
    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)
    banco['Concepto_str'] = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    # Vectorizado: construir pattern con CUITs (dígitos)
    cuit_list = personas['cuit_digits'].dropna().unique().tolist()
    # si la lista está vacía, no hay coincidencias
    if len(cuit_list) == 0:
        return pd.DataFrame(), pd.DataFrame()

    # Escapar y unir; para datasets medianos esto funciona bien
    escaped = [re.escape(x) for x in cuit_list if x != '']
    pattern = '|'.join(escaped)
    # buscar en la columna de dígitos (más robusto)
    mask_any = banco['Concepto_digits'].str.contains(pattern, na=False, regex=True)
    matches = banco[mask_any].copy()

    resultados = []
    for _, m in matches.iterrows():
        concepto = str(m.get(concepto_col, ''))
        concepto_digits = re.sub(r'\D', '', concepto)
        # buscar qué CUIT(s) aparecen en este concepto (puede haber más de uno)
        found = set()
        for c in escaped:
            if re.search(c, concepto_digits):
                found.add(re.sub(r'\\', '', c))  # quitar escapes
        # si no encontramos por dígitos, intentar buscar por texto bruto (cuit_raw)
        if not found:
            for c_raw in personas['cuit_raw'].dropna().unique():
                if c_raw and c_raw.lower() in concepto.lower():
                    found.add(only_digits(c_raw))
        for f in found:
            # obtener persona asociada (primer match)
            p = personas[personas['cuit_digits'] == f]
            if p.empty:
                nombre = ''
                lote = ''
                golf = ''
            else:
                rowp = p.iloc[0]
                nombre = rowp.get(nombre_col, '') if nombre_col else ''
                lote = rowp.get(lote_col, '') if lote_col else ''
                golf = rowp.get(golf_col, '') if golf_col else ''
            credito_val = m.get(credito_col, m.get('Credito','')) if credito_col else m.get('Credito','')
            credito_str = str(credito_val).strip()
            credito_str = re.sub(r'[^\d,.\-]', '', credito_str)
            if credito_str.count(',') == 1 and credito_str.count('.') == 0:
                credito_norm = credito_str.replace('.', '').replace(',', '.')
            else:
                credito_norm = credito_str.replace(',', '')
            credito_num = pd.to_numeric(credito_norm, errors='coerce')
            fecha_val = m.get(fecha_col, '') if fecha_col else ''
            fecha_dt = pd.to_datetime(fecha_val, dayfirst=True, errors='coerce')

            resultados.append({
                'Fecha': fecha_dt,
                'Cuit/Cuil': p.iloc[0]['cuit_raw'] if not p.empty else f,
                'Nombre': nombre,
                'Lote': lote,
                'Golf': golf,
                'Valor_num': credito_num,
                'Valor_raw': credito_val,
                'Concepto encontrado': concepto
            })

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty:
        return pd.DataFrame(), pd.DataFrame()

    df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], dayfirst=True, errors='coerce')
    df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
    df_detalle['Fecha_str'] = df_detalle['Fecha'].dt.strftime('%Y-%m-%d').fillna('')
    df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    df_detalle_display = df_detalle[['Fecha_str','Cuit/Cuil','Nombre','Lote','Golf','Valor_formateado','Concepto encontrado']].copy()
    df_detalle_display = df_detalle_display.rename(columns={'Fecha_str': 'Fecha','Valor_formateado': 'Valor transferido'})

    df_resumen = df_detalle.copy()
    df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)
    resumen = df_resumen.groupby(['Cuit/Cuil','Nombre','Lote','Golf'], as_index=False)['Valor_num'].sum()
    resumen = resumen.rename(columns={'Valor_num':'Suma_total_num'})
    resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')
    resumen_display = resumen[['Cuit/Cuil','Nombre','Lote','Golf','Suma total','Suma_total_num']].copy()
    res_sorted = resumen_display.sort_values('Suma_total_num', ascending=False)

    return df_detalle_display, res_sorted

def format_currency_ar(value):
    try:
        v = float(value)
    except Exception:
        return '' if value is None else str(value)
    sign = '-' if v < 0 else ''
    v_abs = abs(v)
    s = f"{v_abs:,.2f}"
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return sign + s

# ---------- UI: subida ----------
col1, col2 = st.columns(2)
with col1:
    uploaded_personas = st.file_uploader("Sube Excel con Cuit/Cuil, Nombre, Lote, Golf", type=["xls","xlsx"], key="u_personas")
with col2:
    uploaded_banco = st.file_uploader("Sube Excel de movimientos (Concepto, Fecha, Crédito)", type=["xls","xlsx"], key="u_banco")

# guardar bytes en session_state
if uploaded_personas is not None:
    st.session_state['uploaded_personas_bytes'] = uploaded_personas.read()
    st.session_state['uploaded_personas_name'] = getattr(uploaded_personas, "name", "")
if uploaded_banco is not None:
    st.session_state['uploaded_banco_bytes'] = uploaded_banco.read()
    st.session_state['uploaded_banco_name'] = getattr(uploaded_banco, "name", "")

st.write(" ")
with st.form("procesar_form"):
    st.write("Pulsa Procesar archivos para extraer coincidencias.")
    submit = st.form_submit_button("Procesar archivos")
    if submit:
        if not st.session_state.get('uploaded_personas_bytes') or not st.session_state.get('uploaded_banco_bytes'):
            st.error("Subí ambos archivos antes de procesar.")
        else:
            try:
                df_detalle_display, res_sorted = process_files(
                    st.session_state['uploaded_personas_bytes'],
                    st.session_state['uploaded_banco_bytes'],
                    st.session_state.get('uploaded_personas_name',''),
                    st.session_state.get('uploaded_banco_name','')
                )
            except Exception as e:
                st.error(f"Error en procesamiento: {e}")
                st.stop()

            if df_detalle_display.empty:
                st.info("No se encontraron coincidencias.")
                st.session_state['df_detalle_display'] = None
                st.session_state['res_sorted'] = None
                st.session_state['processed'] = False
            else:
                st.session_state['df_detalle_display'] = df_detalle_display
                st.session_state['res_sorted'] = res_sorted
                st.session_state['processed'] = True
                st.success("Procesamiento finalizado y resultados guardados.")

# ---------- helpers AgGrid ----------
def show_aggrid(df, height=400, page_size=25):
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=page_size)
    gb.configure_default_column(filterable=True, sortable=True, resizable=True)
    gb.configure_grid_options(domLayout='normal')
    gridOptions = gb.build()
    AgGrid(
        df,
        gridOptions=gridOptions,
        height=height,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        update_mode=GridUpdateMode.NO_UPDATE,
        allow_unsafe_jscode=True,
    )

# ---------- Mostrar tablas persistentes (Ag-Grid) ----------
if st.session_state.get('df_detalle_display') is not None:
    st.markdown("---")
    st.subheader("Detalle guardado")
    show_aggrid(st.session_state['df_detalle_display'], height=400)
    # descarga CSV
    csv_det = st.session_state['df_detalle_display'].to_csv(index=False).encode('utf-8')
    st.download_button("Descargar detalle CSV", data=csv_det, file_name="detalle.csv", mime="text/csv")

if st.session_state.get('res_sorted') is not None:
    st.markdown("---")
    st.subheader("Resumen guardado")
    show_aggrid(st.session_state['res_sorted'][['Cuit/Cuil','Nombre','Lote','Golf','Suma total']], height=300)
    csv_res = st.session_state['res_sorted'].to_csv(index=False).encode('utf-8')
    st.download_button("Descargar resumen CSV", data=csv_res, file_name="resumen.csv", mime="text/csv")

# ---------- Buscador por Lote persistente ----------
st.markdown("---")
st.subheader("Buscar por Lote (resalta coincidencias)")

search_lote = st.text_input("Ingresá número de lote para buscar (ej: 41)", value=st.session_state.get('search_lote',''), key="search_lote")
# no reasignar st.session_state['search_lote'] manualmente

if search_lote and st.session_state.get('df_detalle_display') is not None:
    search_lower = str(search_lote).strip().lower()
    df_det = st.session_state['df_detalle_display']
    mask_det = df_det['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
    matches_det = df_det[mask_det]
    count_det = len(matches_det)

    res_sorted = st.session_state['res_sorted']
    mask_res = res_sorted['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
    matches_res = res_sorted[mask_res]
    count_res = len(matches_res)

    st.write(f"Coincidencias en detalle: **{count_det}** — Coincidencias en resumen: **{count_res}**")

    if count_det > 0:
        show_aggrid(matches_det, height=300)
    else:
        st.info("No se encontraron filas en el detalle para ese lote.")

    if count_res > 0:
        show_aggrid(matches_res[['Cuit/Cuil','Nombre','Lote','Golf','Suma total']], height=250)
    else:
        st.info("No se encontraron filas en el resumen para ese lote.")

st.caption(f"Versión de la app: {VERSION}")