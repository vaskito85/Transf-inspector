# streamlit_app.py
import io
import re
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode
from PIL import Image, ImageDraw

st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide")
VERSION = "5.5"

# ---------- pequeño logo a la izquierda del título ----------
def make_logo(size=48, bg_color=(255, 255, 255, 0), circle_color=(25, 118, 210, 255)):
    img = Image.new("RGBA", (size, size), bg_color)
    draw = ImageDraw.Draw(img)
    margin = int(size * 0.12)
    draw.ellipse([margin, margin, size - margin, size - margin], fill=circle_color)
    inner = int(size * 0.28)
    draw.ellipse([size//2 - inner//2, size//2 - inner//2, size//2 + inner//2, size//2 + inner//2], fill=(255,255,255,200))
    return img

col_logo, col_title = st.columns([0.6, 9.4])
with col_logo:
    st.image(make_logo(size=48), width=48)
with col_title:
    st.title("Buscador CUIT - Movimientos")

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

def try_cast_int_series(s: pd.Series) -> pd.Series:
    """Si todos los valores no nulos son enteros, devuelve la serie como Int64; si no, devuelve la serie original."""
    try:
        non_null = s.dropna()
        if non_null.empty:
            return s
        numeric = pd.to_numeric(non_null, errors='coerce')
        if numeric.isna().any():
            return s
        if (numeric % 1 == 0).all():
            return pd.to_numeric(s, errors='coerce').astype('Int64')
        return s
    except Exception:
        return s

# Helper seguro para formatear Lote/Golf antes de exportar (robusto contra '')
def format_for_export(v):
    # NaN o valores vacíos -> cadena vacía
    if pd.isna(v):
        return ''
    # Si es pandas nullable Int (Int64) o numpy int
    try:
        # pandas Int64 scalar
        if hasattr(v, 'dtype') and str(getattr(v, 'dtype')) == 'Int64':
            try:
                return str(int(v))
            except Exception:
                pass
    except Exception:
        pass
    # Python int
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    # numpy integer-like
    try:
        import numpy as np
        if isinstance(v, (np.integer,)):
            return str(int(v))
    except Exception:
        pass
    # float
    if isinstance(v, float):
        if float(v).is_integer():
            return str(int(v))
        return str(v)
    # pandas scalar with .item()
    try:
        if hasattr(v, 'item'):
            vv = v.item()
            if isinstance(vv, int) and not isinstance(vv, bool):
                return str(vv)
            if isinstance(vv, float):
                if float(vv).is_integer():
                    return str(int(vv))
                return str(vv)
    except Exception:
        pass
    # string
    if isinstance(v, str):
        s = v.strip()
        if s == '':
            return ''
        if re.fullmatch(r'[+-]?\d+', s):
            return s
        if re.fullmatch(r'[+-]?\d+\.\d+', s):
            try:
                f = float(s)
                if f.is_integer():
                    return str(int(f))
            except Exception:
                pass
        return s
    # fallback
    try:
        return str(v)
    except Exception:
        return ''

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

# ---------- procesamiento principal (vectorizado, con columnas raw y num) ----------
@st.cache_data
def process_files(personas_bytes, banco_bytes, personas_name, banco_name):
    personas = read_excel_bytes_from_buffer(personas_bytes, ext_hint=(personas_name.split('.')[-1] if personas_name else None))
    banco = read_excel_bytes_from_buffer(banco_bytes, ext_hint=(banco_name.split('.')[-1] if banco_name else None))

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

    personas = personas.copy()
    banco = banco.copy()

    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)
    banco['Concepto_str'] = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    cuit_list = personas['cuit_digits'].dropna().unique().tolist()
    if len(cuit_list) == 0:
        return pd.DataFrame(), pd.DataFrame()

    escaped = [re.escape(x) for x in cuit_list if x != '']
    pattern = '|'.join(escaped)
    mask_any = banco['Concepto_digits'].str.contains(pattern, na=False, regex=True)
    matches = banco[mask_any].copy()

    resultados = []
    for _, m in matches.iterrows():
        concepto = str(m.get(concepto_col, ''))
        concepto_digits = re.sub(r'\D', '', concepto)
        found = set()
        for c in escaped:
            if re.search(c, concepto_digits):
                found.add(re.sub(r'\\', '', c))
        if not found:
            for c_raw in personas['cuit_raw'].dropna().unique():
                if c_raw and c_raw.lower() in concepto.lower():
                    found.add(only_digits(c_raw))
        for f in found:
            p = personas[personas['cuit_digits'] == f]
            if p.empty:
                nombre = ''
                lote = ''
                golf = ''
                cuit_display = f
            else:
                rowp = p.iloc[0]
                nombre = rowp.get(nombre_col, '') if nombre_col else ''
                lote = rowp.get(lote_col, '') if lote_col else ''
                golf = rowp.get(golf_col, '') if golf_col else ''
                cuit_display = rowp.get('cuit_raw', f)
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
                'Cuit/Cuil': cuit_display,
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

    # trabajar sobre copia para evitar SettingWithCopyWarning
    df_detalle = df_detalle.copy()
    df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], dayfirst=True, errors='coerce')
    df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
    df_detalle['Fecha_str'] = df_detalle['Fecha'].dt.strftime('%Y-%m-%d').fillna('')
    df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    # --- mantener columnas raw para agrupación/búsqueda y columnas numéricas separadas para ordenamiento ---
    for _col in ['Lote', 'Golf']:
        if _col in df_detalle.columns:
            df_detalle[f"{_col}_raw"] = df_detalle[_col].astype(str).replace('nan', '').replace('None', '')
            df_detalle[f"{_col}_num"] = pd.to_numeric(df_detalle[_col].replace('', pd.NA), errors='coerce')

    # Preparar df_detalle_display: incluir raw y num (guardamos ambos)
    df_detalle_display = df_detalle[['Fecha_str','Cuit/Cuil','Nombre','Lote_raw','Golf_raw','Lote_num','Golf_num','Valor_formateado','Concepto encontrado']].copy()
    df_detalle_display = df_detalle_display.rename(columns={
        'Fecha_str': 'Fecha',
        'Lote_raw': 'Lote',
        'Golf_raw': 'Golf',
        'Valor_formateado': 'Valor transferido'
    })

    # Construir resumen agrupando por las columnas raw (evitamos perder grupos por coerción)
    df_resumen = df_detalle.copy()
    df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)

    resumen = df_resumen.groupby(['Cuit/Cuil','Nombre','Lote_raw','Golf_raw'], as_index=False)['Valor_num'].sum()
    resumen = resumen.rename(columns={'Valor_num':'Suma_total_num'})
    resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    # Mapear valores numéricos para ordenamiento en resumen
    map_lote_num = df_detalle.dropna(subset=['Lote_num']).drop_duplicates('Lote_raw').set_index('Lote_raw')['Lote_num'].to_dict()
    map_golf_num = df_detalle.dropna(subset=['Golf_num']).drop_duplicates('Golf_raw').set_index('Golf_raw')['Golf_num'].to_dict()

    resumen['Lote_num'] = resumen['Lote_raw'].map(map_lote_num)
    resumen['Golf_num'] = resumen['Golf_raw'].map(map_golf_num)

    resumen_display = resumen[['Cuit/Cuil','Nombre','Lote_raw','Golf_raw','Suma total','Suma_total_num','Lote_num','Golf_num']].copy()
    resumen_display = resumen_display.rename(columns={'Lote_raw':'Lote','Golf_raw':'Golf'})

    # ---------- Ajuste final: convertir columnas numéricas a Int64 cuando sea posible
    if 'Lote_num' in df_detalle_display.columns:
        df_detalle_display['Lote_num'] = try_cast_int_series(df_detalle_display['Lote_num'])
    if 'Golf_num' in df_detalle_display.columns:
        df_detalle_display['Golf_num'] = try_cast_int_series(df_detalle_display['Golf_num'])

    if 'Lote_num' in resumen_display.columns:
        resumen_display['Lote_num'] = try_cast_int_series(resumen_display['Lote_num'])
    if 'Golf_num' in resumen_display.columns:
        resumen_display['Golf_num'] = try_cast_int_series(resumen_display['Golf_num'])

    # Para la vista: preferir la columna numérica (sin .0 si fue casteada a Int64),
    # y si no existe, dejar la versión textual original.
    def prefer_num_or_raw(df, col_raw, col_num):
        df = df.copy()
        if col_num in df.columns:
            df[col_raw] = df[col_num].where(df[col_num].notna(), df[col_raw])
        return df

    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Lote', 'Lote_num')
    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Golf', 'Golf_num')

    resumen_display = prefer_num_or_raw(resumen_display, 'Lote', 'Lote_num')
    resumen_display = prefer_num_or_raw(resumen_display, 'Golf', 'Golf_num')

    # Ordenar resumen por suma
    res_sorted = resumen_display.sort_values('Suma_total_num', ascending=False)

    # Antes de devolver: normalizar nombres y devolver copias limpias
    df_detalle_display_to_save = df_detalle_display.copy()
    res_sorted_to_save = res_sorted.copy()

    return df_detalle_display_to_save, res_sorted_to_save

# ---------- UI: subida ----------
col1, col2 = st.columns(2)
with col1:
    uploaded_personas = st.file_uploader("Sube Excel con Cuit/Cuil, Nombre, Lote, Golf", type=["xls","xlsx"], key="u_personas")
with col2:
    uploaded_banco = st.file_uploader("Sube Excel de movimientos (Concepto, Fecha, Crédito)", type=["xls","xlsx"], key="u_banco")

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
                # Guardar copias limpias en session_state
                st.session_state['df_detalle_display'] = df_detalle_display.copy()
                st.session_state['res_sorted'] = res_sorted.copy()
                st.session_state['processed'] = True
                st.success("Procesamiento finalizado y resultados guardados.")

# ---------- selector page size ----------
page_choice = st.selectbox("Tamaño de página", options=["25","50","75","100","All"], index=0)
page_size = None if page_choice == "All" else int(page_choice)

# ---------- helper AgGrid (rangos, copia, paginación opcional) ----------
def show_aggrid(df, height=400, page_size=25):
    df_display = df.copy()
    gb = GridOptionsBuilder.from_dataframe(df_display)
    gb.configure_default_column(filterable=True, sortable=True, resizable=True)
    gb.configure_grid_options(enableRangeSelection=True, enableFillHandle=True, suppressCopyRowsToClipboard=False)
    if page_size is None:
        gb.configure_grid_options(pagination=False)
    else:
        gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=page_size)
    gridOptions = gb.build()
    gridOptions.setdefault('enableRangeSelection', True)
    gridOptions.setdefault('clipboardDelimiter', '\t')
    AgGrid(
        df_display,
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
    df_det_show = st.session_state['df_detalle_display'].copy()
    cols_det = ['Fecha','Cuit/Cuil','Nombre','Lote','Golf','Valor transferido','Concepto encontrado']
    cols_det = [c for c in cols_det if c in df_det_show.columns]
    show_aggrid(df_det_show[cols_det], height=400, page_size=page_size)
    # Para descarga: formatear Lote/Golf de forma segura
    df_det_export = df_det_show[cols_det].copy()
    # Reemplazar cadenas vacías por NA para evitar int('') en conversiones internas
    for c in ['Lote','Golf']:
        if c in df_det_export.columns:
            df_det_export[c] = df_det_export[c].replace('', pd.NA)
            df_det_export[c] = df_det_export[c].apply(format_for_export)
    csv_det = df_det_export.to_csv(index=False).encode('utf-8')
    st.download_button("Descargar detalle CSV", data=csv_det, file_name="detalle.csv", mime="text/csv")

if st.session_state.get('res_sorted') is not None:
    st.markdown("---")
    st.subheader("Resumen guardado")
    res_sorted_df = st.session_state['res_sorted'].copy()
    cols_res = ['Cuit/Cuil','Nombre','Lote','Golf','Suma total']
    cols_res = [c for c in cols_res if c in res_sorted_df.columns]
    show_aggrid(res_sorted_df[cols_res], height=300, page_size=page_size)
    df_res_export = res_sorted_df[cols_res + (['Suma_total_num'] if 'Suma_total_num' in res_sorted_df.columns else [])].copy()
    for c in ['Lote','Golf']:
        if c in df_res_export.columns:
            df_res_export[c] = df_res_export[c].replace('', pd.NA)
            df_res_export[c] = df_res_export[c].apply(format_for_export)
    csv_res = df_res_export.to_csv(index=False).encode('utf-8')
    st.download_button("Descargar resumen CSV", data=csv_res, file_name="resumen.csv", mime="text/csv")

# ---------- Buscador por Lote persistente (filtrado seguro sin SettingWithCopyWarning) ----------
st.markdown("---")
st.subheader("Buscar por Lote (resalta coincidencias)")

search_lote = st.text_input("Ingresá número de lote para buscar (ej: 41)", value=st.session_state.get('search_lote',''), key="search_lote")

if search_lote and st.session_state.get('df_detalle_display') is not None:
    search_lower = str(search_lote).strip().lower()
    df_det = st.session_state['df_detalle_display']
    mask_det = df_det['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
    matches_det = df_det.loc[mask_det].copy()
    count_det = len(matches_det)

    res_sorted = st.session_state['res_sorted']
    mask_res = res_sorted['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
    matches_res = res_sorted.loc[mask_res].copy()
    count_res = len(matches_res)

    st.write(f"Coincidencias en detalle: **{count_det}** — Coincidencias en resumen: **{count_res}**")

    if count_det > 0:
        cols_det = ['Fecha','Cuit/Cuil','Nombre','Lote','Golf','Valor transferido','Concepto encontrado']
        cols_det = [c for c in cols_det if c in matches_det.columns]
        show_aggrid(matches_det[cols_det], height=300, page_size=page_size)
    else:
        st.info("No se encontraron filas en el detalle para ese lote.")

    if count_res > 0:
        cols_res = ['Cuit/Cuil','Nombre','Lote','Golf','Suma total']
        cols_res = [c for c in cols_res if c in matches_res.columns]
        show_aggrid(matches_res[cols_res], height=250, page_size=page_size)
    else:
        st.info("No se encontraron filas en el resumen para ese lote.")

st.caption(f"Versión de la app: {VERSION}")