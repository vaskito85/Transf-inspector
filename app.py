import io
import re
import math
import traceback
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode
from PIL import Image, ImageDraw

# =========================
# Config general
# =========================
st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide")
VERSION = "6.1.0"

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
st.caption(f"Versión de la app: {VERSION}")

# =========================
# Session state inicial
# =========================
for key, default in {
    'uploaded_personas_bytes': None,
    'uploaded_banco_bytes': None,
    'uploaded_personas_name': '',
    'uploaded_banco_name': '',
    'df_detalle_display': None,
    'res_sorted': None,
    'processed': False,
    'search_lote': '',
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# =========================
# Utilidades
# =========================
def only_digits(s):
    return re.sub(r'\D', '', str(s) if s is not None else '')

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

def is_integer_string(s: str) -> bool:
    return bool(re.fullmatch(r'[+-]?\d+', s.strip()))

def is_float_string(s: str) -> bool:
    return bool(re.fullmatch(r'[+-]?\d+\.\d+', s.strip()))

def safe_int_like_to_str(v):
    try:
        if pd.isna(v):
            return ''
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        try:
            import numpy as np
            if isinstance(v, (np.integer,)):
                return str(int(v))
        except Exception:
            pass
        if hasattr(v, 'item'):
            vv = v.item()
            if isinstance(vv, int) and not isinstance(vv, bool):
                return str(vv)
            if isinstance(vv, float):
                if float(vv).is_integer():
                    return str(int(vv))
                return str(vv)
        if isinstance(v, float):
            if float(v).is_integer():
                return str(int(v))
            return str(v)
        if isinstance(v, str):
            s = v.strip()
            if s == '':
                return ''
            if is_integer_string(s):
                return s
            if is_float_string(s):
                try:
                    f = float(s)
                    if f.is_integer():
                        return str(int(f))
                except Exception:
                    pass
            return s
        s = str(v).strip()
        if s == '':
            return ''
        if is_integer_string(s):
            return s
        if is_float_string(s):
            try:
                f = float(s)
                if f.is_integer():
                    return str(int(f))
            except Exception:
                pass
        return s
    except Exception:
        return ''

def try_cast_int_series_safe(s: pd.Series) -> pd.Series:
    try:
        s2 = s.copy()
        numeric = pd.to_numeric(s2, errors='coerce')
        non_null = numeric.dropna()
        if non_null.empty:
            return s2
        if (non_null % 1 == 0).all():
            return numeric.astype('Int64')
        return s2
    except Exception:
        return s

def unique_join(values):
    seen, result = set(), []
    for v in values:
        vv = (str(v) if v is not None else '').strip()
        if vv and vv.lower() not in ('nan', 'none') and vv not in seen:
            seen.add(vv); result.append(vv)
    return " / ".join(result)

# ---------- Detección de columnas mejorada ----------
def find_col(df: pd.DataFrame, keywords):
    """
    Busca columnas dando prioridad a:
    1) match exacto
    2) empieza con
    3) contiene
    """
    cols = [str(c) for c in df.columns]
    kl = [k.lower() for k in keywords]

    # exact
    for k in kl:
        for c in cols:
            if c.lower() == k:
                return c
    # startswith
    for k in kl:
        for c in cols:
            if c.lower().startswith(k):
                return c
    # contains
    for k in kl:
        for c in cols:
            if k in c.lower():
                return c
    return None

# ---------- Parseo robusto de importes ----------
def parse_money_ar(s: str):
    """
    Normaliza y parsea importes: 1.234,56 | 1,234.56 | 1234,56 | 1234.56 | (1.234,56) | $ 1.234,56
    Devuelve float o NaN si no parsea.
    """
    if s is None:
        return float('nan')
    if isinstance(s, (int, float)):
        try:
            return float(s)
        except Exception:
            return float('nan')

    s = str(s).strip()
    if s == '':
        return float('nan')

    # eliminar símbolos no numéricos comunes (menos signos, comas, puntos, paréntesis)
    s = re.sub(r'[^\d,.\-\(\)]', '', s)

    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg = True
        s = s[1:-1]

    last_comma = s.rfind(',')
    last_dot   = s.rfind('.')

    if last_comma == -1 and last_dot == -1:
        # no separador decimal: sacar posibles miles
        s_clean = re.sub(r'[,.]', '', s)
    else:
        dec = ',' if last_comma > last_dot else '.'
        idx = last_comma if dec == ',' else last_dot
        int_part = re.sub(r'[.,]', '', s[:idx])
        dec_part = s[idx+1:]
        s_clean = f"{int_part}.{dec_part}"

    try:
        val = float(s_clean)
        if neg:
            val = -val
        return val
    except Exception:
        return float('nan')

# ---------- Créditos partidos (plan B) ----------
def get_credit_value_from_row(row: pd.Series, credito_col: str, df_cols: list, max_look_ahead: int = 2):
    """
    Toma el valor de 'Crédito'; si es simbólico ('$', '-', vacío), busca en hasta 2 columnas contiguas a la derecha.
    """
    def looks_symbolic(x: str) -> bool:
        s = str(x).strip()
        return (s == '' or s in ('$', '-', '$-', '-$') or re.fullmatch(r'[\$\-\s]+', s) is not None)

    # valor en la columna detectada
    val = row.get(credito_col, '')
    if not looks_symbolic(val) and re.search(r'\d', str(val)):
        return val

    # buscar a la derecha (1..max_look_ahead)
    try:
        j = df_cols.index(credito_col)
        for k in range(1, max_look_ahead + 1):
            if j + k < len(df_cols):
                vnext = row.get(df_cols[j + k], '')
                if not looks_symbolic(vnext) and re.search(r'\d', str(vnext)):
                    return vnext
    except Exception:
        pass
    return val  # devolvemos lo original si no encontramos mejor

# ---------- Validación CUIT ----------
CUIT_LEN = 11

def cuit_is_valid(cuit_digits: str) -> bool:
    if not re.fullmatch(r'\d{11}', cuit_digits or ''):
        return False
    digits = list(map(int, cuit_digits))
    factors = [5,4,3,2,7,6,5,4,3,2]
    s = sum(d * f for d, f in zip(digits[:10], factors))
    mod = 11 - (s % 11)
    check = 0 if mod == 11 else (9 if mod == 10 else mod)
    return check == digits[10]

def extract_digit_runs(s):
    return re.findall(r'\d{7,}', s or '')  # ignorar runs cortas para optimizar

def find_cuits_in_text(concepto_digits: str):
    """
    Devuelve todos los substrings de 11 dígitos presentes en corridas numéricas del concepto.
    No valida; la validación (si corresponde) se aplica afuera.
    """
    found = set()
    if not concepto_digits:
        return found
    for run in extract_digit_runs(concepto_digits):
        if len(run) < CUIT_LEN:
            continue
        for i in range(len(run) - CUIT_LEN + 1):
            sub = run[i:i+CUIT_LEN]
            found.add(sub)
    return found

# =========================
# Lectura segura de Excel
# =========================
@st.cache_data
def read_excel_bytes_from_buffer(buf_bytes, ext_hint=None):
    if not buf_bytes:
        raise ValueError("Archivo vacío o no cargado.")
    buf = io.BytesIO(buf_bytes)
    try:
        if ext_hint and ext_hint.lower() == "xls":
            # xlrd para .xls (si está disponible en el entorno)
            return pd.read_excel(buf, dtype=str, engine="xlrd").fillna('')
        else:
            # openpyxl para .xlsx
            return pd.read_excel(buf, dtype=str, engine="openpyxl").fillna('')
    except Exception:
        # Fallback: que Pandas decida engine si no están instalados los recomendados
        buf.seek(0)
        return pd.read_excel(buf, dtype=str).fillna('')

# =========================
# Procesamiento principal
# =========================
@st.cache_data(show_spinner=False)
def process_files(personas_bytes, banco_bytes, personas_name, banco_name, validate_cuit=True, show_unknown=False):
    # ---- leer
    personas = read_excel_bytes_from_buffer(
        personas_bytes,
        ext_hint=(personas_name.split('.')[-1] if personas_name else None)
    )
    banco = read_excel_bytes_from_buffer(
        banco_bytes,
        ext_hint=(banco_name.split('.')[-1] if banco_name else None)
    )

    # ---- detectar columnas banco
    concepto_col = find_col(banco, ['concepto', 'concept'])
    credito_col  = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto'])
    fecha_col    = find_col(banco, ['fecha', 'date', 'fecha de'])
    if not concepto_col:
        raise ValueError("No se encontró la columna de Concepto en el archivo bancario.")
    if not credito_col:
        raise ValueError("No se encontró columna de Crédito/Importe/Monto en el archivo bancario.")

    # ---- detectar columnas personas
    cuit_col   = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col   = find_col(personas, ['lote'])
    golf_col   = find_col(personas, ['golf'])
    if not cuit_col:
        raise ValueError("No se encontró la columna de Cuit/Cuil en el archivo de personas.")

    personas = personas.copy()
    banco = banco.copy()

    # ---- normalizaciones personas
    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)

    cuit_list = [c for c in personas['cuit_digits'].dropna().unique().tolist() if c]
    if validate_cuit:
        cuit_list = [c for c in cuit_list if cuit_is_valid(c)]
    cuit_set = set(cuit_list)

    # si no hay ningún CUIT en personas y show_unknown es False, no habrá resultados
    # con show_unknown=True sí puede haber (a partir del banco).
    if len(cuit_list) == 0 and not show_unknown:
        return pd.DataFrame(), pd.DataFrame()

    # ---- normalizaciones banco
    banco['Concepto_str'] = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    # ---- escanear movimientos y capturar cuits encontrados
    matches_idx = []
    found_map = {}  # idx banco -> set(cuits_encontrados)
    for idx, row in banco.iterrows():
        concepto_digits = row['Concepto_digits']
        # 1) todos los substrings de 11 dígitos en el concepto
        found_all = find_cuits_in_text(concepto_digits)

        # 2) si validar dígito: filtrar los que no cumplan
        if validate_cuit:
            found_all = {c for c in found_all if cuit_is_valid(c)}

        # 3) aplicar política según show_unknown:
        #    - False: solo cuits listados en Personas
        #    - True: todos los encontrados (listados y no listados)
        if show_unknown:
            found = set(found_all)
        else:
            found = set(found_all) & cuit_set

        # 4) fallback textual si no encontró nada por dígitos:
        if not found:
            concepto = str(row.get(concepto_col, ''))
            found_raw = set()
            for c_raw in personas['cuit_raw'].dropna().unique():
                if c_raw and c_raw.strip() and c_raw.lower() in concepto.lower():
                    cd = only_digits(c_raw)
                    if (not validate_cuit) or cuit_is_valid(cd):
                        found_raw.add(cd)
            found = found or found_raw

        if found:
            matches_idx.append(idx)
            found_map[idx] = found

    if not matches_idx:
        return pd.DataFrame(), pd.DataFrame()

    matches = banco.loc[matches_idx].copy()
    banco_cols = list(banco.columns)

    # ---- construir detalle
    resultados = []
    for idx, m in matches.iterrows():
        concepto = str(m.get(concepto_col, ''))
        fecha_val = m.get(fecha_col, '') if fecha_col else ''
        fecha_dt = pd.to_datetime(fecha_val, dayfirst=True, errors='coerce')

        # valor (tomamos "Crédito" con plan B a derecha si está partido)
        credito_val = get_credit_value_from_row(m, credito_col, banco_cols, max_look_ahead=2)
        credito_num = parse_money_ar(credito_val)

        for f in found_map.get(idx, []):
            # Buscar datos en Personas (si no está, quedarán vacíos)
            p = personas[personas['cuit_digits'] == f]
            if p.empty:
                nombre = ''
                lote = ''
                golf = ''
                cuit_display = f  # para desconocidos, mostramos los 11 dígitos
            else:
                nombres_unique = [n for n in pd.unique(p[nombre_col].astype(str).str.strip())] if nombre_col else []
                lotes_unique = [l for l in pd.unique(p[lote_col].astype(str).str.strip())] if lote_col else []
                golfs_unique = [g for g in pd.unique(p[golf_col].astype(str).str.strip())] if golf_col else []

                nombre = unique_join(nombres_unique)
                lote   = unique_join(lotes_unique)
                golf   = unique_join(golfs_unique)

                cuit_raws = p['cuit_raw'].astype(str).str.strip()
                cuit_display = (pd.unique(cuit_raws)[0] if len(pd.unique(cuit_raws)) > 0 else f)

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

    # ---- orden/formatos detalle
    df_detalle = df_detalle.copy()
    df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], dayfirst=True, errors='coerce')
    df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
    df_detalle['Fecha_str'] = df_detalle['Fecha'].dt.strftime('%Y-%m-%d').fillna('')
    df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    for _col in ['Lote', 'Golf']:
        if _col in df_detalle.columns:
            df_detalle[f"{_col}_raw"] = df_detalle[_col].astype(str).replace('nan', '').replace('None', '')
            df_detalle[f"{_col}_num"] = pd.to_numeric(df_detalle[_col].replace('', pd.NA), errors='coerce')

    # ---- df_detalle_display
    cols_base = ['Fecha_str','Cuit/Cuil','Nombre','Lote_raw','Golf_raw','Lote_num','Golf_num','Valor_formateado','Concepto encontrado']
    cols_base = [c for c in cols_base if c in df_detalle.columns]
    df_detalle_display = df_detalle[cols_base].copy()
    df_detalle_display = df_detalle_display.rename(columns={
        'Fecha_str': 'Fecha',
        'Lote_raw': 'Lote',
        'Golf_raw': 'Golf',
        'Valor_formateado': 'Valor transferido'
    })

    # ---- resumen por CUIT/Lote/Golf
    df_resumen = df_detalle.copy()
    df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)

    # nombres concatenados por CUIT
    nombres_map = df_resumen.groupby('Cuit/Cuil')['Nombre'].apply(lambda x: unique_join(x)).to_dict()

    resumen = df_resumen.groupby(['Cuit/Cuil','Lote_raw','Golf_raw'], as_index=False)['Valor_num'].sum()
    resumen['Nombre'] = resumen['Cuit/Cuil'].map(nombres_map)
    resumen = resumen.rename(columns={'Valor_num': 'Suma_total_num'})
    resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    # map de num para orden
    map_lote_num = df_detalle.dropna(subset=['Lote_num']).drop_duplicates('Lote_raw').set_index('Lote_raw')['Lote_num'].to_dict()
    map_golf_num = df_detalle.dropna(subset=['Golf_num']).drop_duplicates('Golf_raw').set_index('Golf_raw')['Golf_num'].to_dict()
    resumen['Lote_num'] = resumen['Lote_raw'].map(map_lote_num)
    resumen['Golf_num'] = resumen['Golf_raw'].map(map_golf_num)

    resumen_display = resumen[['Cuit/Cuil','Nombre','Lote_raw','Golf_raw','Suma total','Suma_total_num','Lote_num','Golf_num']].copy()
    resumen_display = resumen_display.rename(columns={'Lote_raw':'Lote','Golf_raw':'Golf'})

    # ---- Int64 cuando aplique
    for df_ in (df_detalle_display, resumen_display):
        if 'Lote_num' in df_.columns:
            df_['Lote_num'] = try_cast_int_series_safe(df_['Lote_num'])
        if 'Golf_num' in df_.columns:
            df_['Golf_num'] = try_cast_int_series_safe(df_['Golf_num'])

    # ---- preferir num si existe
    def prefer_num_or_raw(df, col_raw, col_num):
        df = df.copy()
        if col_num in df.columns:
            col_num_obj = df[col_num].astype(object)
            col_raw_obj = df[col_raw].astype(object) if col_raw in df.columns else pd.Series([''] * len(df), index=df.index, dtype=object)
            df[col_raw] = col_num_obj.where(pd.notna(col_num_obj), col_raw_obj)
        return df

    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Lote', 'Lote_num')
    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Golf', 'Golf_num')

    resumen_display = prefer_num_or_raw(resumen_display, 'Lote', 'Lote_num')
    resumen_display = prefer_num_or_raw(resumen_display, 'Golf', 'Golf_num')

    # ---- ordenar resumen por suma desc
    res_sorted = resumen_display.sort_values('Suma_total_num', ascending=False)

    # ---- devolver copias
    return df_detalle_display.copy(), res_sorted.copy()

# =========================
# UI: subida de archivos
# =========================
st.markdown("### Archivos de entrada")
col1, col2 = st.columns(2)
with col1:
    uploaded_personas = st.file_uploader("Subí Excel de **personas** (Cuit/Cuil, Nombre, Lote, Golf)", type=["xls","xlsx"], key="u_personas")
with col2:
    uploaded_banco = st.file_uploader("Subí Excel de **movimientos** (Concepto, Fecha, Crédito)", type=["xls","xlsx"], key="u_banco")

if uploaded_personas is not None:
    st.session_state['uploaded_personas_bytes'] = uploaded_personas.read()
    st.session_state['uploaded_personas_name'] = getattr(uploaded_personas, "name", "")
if uploaded_banco is not None:
    st.session_state['uploaded_banco_bytes'] = uploaded_banco.read()
    st.session_state['uploaded_banco_name'] = getattr(uploaded_banco, "name", "")

st.write(" ")

# =========================
# Parámetros de procesamiento
# =========================
st.markdown("### Parámetros")
colp1, colp2, colp3, colp4 = st.columns([1.6, 1.4, 1.6, 2.4])
with colp1:
    validate_cuit = st.checkbox("Validar dígito de CUIT", value=True, help="Reduce falsos positivos en textos con números largos.")
with colp2:
    show_unknown = st.checkbox("Incluir CUITs no listados", value=True, help="Muestra movimientos con CUIT en concepto aunque no estén en Personas.")
with colp3:
    page_choice = st.selectbox("Tamaño de página", options=["25","50","75","100","200","All"], index=0)
    page_size = None if page_choice == "All" else int(page_choice)
with colp4:
    exact_lote = st.checkbox("Búsqueda de Lote exacta", value=False)

# =========================
# Procesar
# =========================
with st.form("procesar_form"):
    st.write("Pulsa **Procesar archivos** para extraer coincidencias.")
    submit = st.form_submit_button("Procesar archivos")
    if submit:
        try:
            if not st.session_state['uploaded_personas_bytes'] or not st.session_state['uploaded_banco_bytes']:
                st.warning("Subí **ambos** archivos antes de procesar.")
                st.stop()

            df_detalle_display, res_sorted = process_files(
                st.session_state['uploaded_personas_bytes'],
                st.session_state['uploaded_banco_bytes'],
                st.session_state.get('uploaded_personas_name',''),
                st.session_state.get('uploaded_banco_name',''),
                validate_cuit=validate_cuit,
                show_unknown=show_unknown
            )
        except Exception as e:
            st.error(f"Error en procesamiento: {e}")
            st.text("Traceback (detalles técnicos):")
            st.text(traceback.format_exc())

            # Intentar mostrar primeras filas para depuración
            try:
                personas_df = read_excel_bytes_from_buffer(
                    st.session_state['uploaded_personas_bytes'],
                    ext_hint=st.session_state.get('uploaded_personas_name','').split('.')[-1] if st.session_state.get('uploaded_personas_name') else None
                )
                banco_df = read_excel_bytes_from_buffer(
                    st.session_state['uploaded_banco_bytes'],
                    ext_hint=st.session_state.get('uploaded_banco_name','').split('.')[-1] if st.session_state.get('uploaded_banco_name') else None
                )
                st.markdown("**Primeras filas del archivo de personas (depuración):**")
                st.dataframe(personas_df.head(10))
                st.markdown("**Primeras filas del archivo bancario (depuración):**")
                st.dataframe(banco_df.head(10))
            except Exception as e2:
                st.text(f"No se pudieron leer los archivos para depuración: {e2}")

            st.info("Reiniciá la app (detener y volver a ejecutar) si el error persiste.")
            st.stop()

        if df_detalle_display.empty:
            st.info("No se encontraron coincidencias.")
            st.session_state['df_detalle_display'] = None
            st.session_state['res_sorted'] = None
            st.session_state['processed'] = False
        else:
            st.session_state['df_detalle_display'] = df_detalle_display.copy()
            st.session_state['res_sorted'] = res_sorted.copy()
            st.session_state['processed'] = True
            st.success("Procesamiento finalizado y resultados guardados.")

# =========================
# Helper AgGrid
# =========================
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

# =========================
# Mostrar tablas + export
# =========================
if st.session_state.get('df_detalle_display') is not None:
    st.markdown("---")
    st.subheader("Detalle guardado")
    df_det_show = st.session_state['df_detalle_display'].copy()

    cols_det = ['Fecha','Cuit/Cuil','Nombre','Lote','Golf','Valor transferido','Concepto encontrado']
    cols_det = [c for c in cols_det if c in df_det_show.columns]
    show_aggrid(df_det_show[cols_det], height=400, page_size=page_size)

    # Export detalle CSV (Excel-friendly: ; y BOM)
    df_det_export = df_det_show[cols_det].copy()
    for c in ['Lote','Golf']:
        if c in df_det_export.columns:
            df_det_export[c] = df_det_export[c].apply(safe_int_like_to_str)
    csv_det = df_det_export.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("Descargar detalle CSV", data=csv_det, file_name="detalle.csv", mime="text/csv")

if st.session_state.get('res_sorted') is not None:
    st.markdown("---")
    st.subheader("Resumen guardado")
    res_sorted_df = st.session_state['res_sorted'].copy()

    cols_res = ['Cuit/Cuil','Nombre','Lote','Golf','Suma total']
    cols_res = [c for c in cols_res if c in res_sorted_df.columns]
    show_aggrid(res_sorted_df[cols_res], height=300, page_size=page_size)

    # Export resumen CSV (Excel-friendly: ; y BOM)
    cols_res_export = cols_res + (['Suma_total_num'] if 'Suma_total_num' in res_sorted_df.columns else [])
    df_res_export = res_sorted_df[cols_res_export].copy()
    for c in ['Lote','Golf']:
        if c in df_res_export.columns:
            df_res_export[c] = df_res_export[c].apply(safe_int_like_to_str)
    csv_res = df_res_export.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("Descargar resumen CSV", data=csv_res, file_name="resumen.csv", mime="text/csv")

    # Export conjunto a Excel (dos hojas)
    def dfs_to_excel_bytes(**sheets):
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
        bio.seek(0)
        return bio

    excel_bytes = dfs_to_excel_bytes(Detalle=df_det_export, Resumen=df_res_export)
    st.download_button("Descargar Excel (Detalle + Resumen)", data=excel_bytes,
                       file_name="resultados.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# =========================
# Buscador por Lote
# =========================
st.markdown("---")
st.subheader("Buscar por Lote (resalta coincidencias)")
search_lote = st.text_input("Ingresá número de lote para buscar (ej: 41)", value=st.session_state.get('search_lote',''), key="search_lote")

if search_lote and st.session_state.get('df_detalle_display') is not None:
    search_lower = str(search_lote).strip().lower()
    df_det = st.session_state['df_detalle_display']
    if exact_lote:
        mask_det = df_det['Lote'].astype(str).str.strip().str.lower() == search_lower
    else:
        mask_det = df_det['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
    matches_det = df_det.loc[mask_det].copy()
    count_det = len(matches_det)

    res_sorted = st.session_state['res_sorted']
    if exact_lote:
        mask_res = res_sorted['Lote'].astype(str).str.strip().str.lower() == search_lower
    else:
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


