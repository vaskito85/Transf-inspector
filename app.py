# streamlit_app.py
import io
import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide")
st.title("Buscar CUIT en movimientos bancarios")

# Versión de la app
VERSION = "4.6"

# ---------- inicializar session_state ----------
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
def read_excel_bytes(uploaded_file):
    """Lee .xls o .xlsx y normaliza como DataFrame con dtype=str."""
    name = getattr(uploaded_file, "name", "") or ""
    ext = name.split('.')[-1].lower()
    data = uploaded_file.read()
    buf = io.BytesIO(data)

    try:
        if ext == "xlsx":
            return pd.read_excel(buf, dtype=str, engine="openpyxl").fillna('')
        elif ext == "xls":
            buf.seek(0)
            df = pd.read_excel(buf, dtype=str).fillna('')
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            out.seek(0)
            return pd.read_excel(out, dtype=str, engine="openpyxl").fillna('')
        else:
            buf.seek(0)
            return pd.read_excel(buf, dtype=str).fillna('')
    except Exception as e:
        st.error(f"Error leyendo archivo: {e}")
        st.stop()

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

def format_date_iso(d):
    if pd.isna(d):
        return ''
    try:
        return f"{d.year}-{d.month:02d}-{d.day:02d}"
    except Exception:
        try:
            dt = pd.to_datetime(d, errors='coerce')
            if pd.isna(dt):
                return str(d)
            return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"
        except Exception:
            return str(d)

# ---------- UI: subida ----------
col1, col2 = st.columns(2)
with col1:
    file_personas = st.file_uploader(
        "Sube Excel con Cuit/Cuil, Nombre, Lote, Golf",
        type=["xls", "xlsx"],
        key="personas"
    )
with col2:
    file_banco = st.file_uploader(
        "Sube Excel de movimientos (Concepto, Fecha, Crédito)",
        type=["xls", "xlsx"],
        key="banco"
    )

run_button = st.button("Procesar archivos")

# ---------- procesamiento ----------
if file_personas and file_banco and run_button:
    personas = read_excel_bytes(file_personas)
    banco = read_excel_bytes(file_banco)

    concepto_col = find_col(banco, ['concepto', 'concept'])
    credito_col = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto'])
    fecha_col = find_col(banco, ['fecha', 'date', 'fecha de'])
    if not concepto_col:
        st.error("No se encontró la columna 'Concepto' en el archivo bancario.")
        st.stop()

    cuit_col = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col = find_col(personas, ['lote'])
    golf_col = find_col(personas, ['golf'])
    if not cuit_col:
        st.error("No se encontró la columna 'Cuit/Cuil' en el archivo de personas.")
        st.stop()

    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)
    banco['Concepto_str'] = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    resultados = []
    total_personas = len(personas) if len(personas) > 0 else 1
    progress = st.progress(0)

    for idx, p in personas.iterrows():
        cuit_raw = str(p.get('cuit_raw','')).strip()
        if not cuit_raw:
            progress.progress(int((idx+1)/total_personas*100))
            continue
        cuit_digits = only_digits(cuit_raw)
        nombre = p.get(nombre_col, '') if nombre_col else ''
        lote = p.get(lote_col, '') if lote_col else ''
        golf = p.get(golf_col, '') if golf_col else ''

        mask_text = banco['Concepto_str'].str.contains(cuit_raw, case=False, na=False, regex=False)
        mask_digits = banco['Concepto_digits'].str.contains(cuit_digits, na=False, regex=False) if cuit_digits else False
        mask = mask_text | mask_digits
        matches = banco[mask]

        for _, m in matches.iterrows():
            credito_val = m.get(credito_col, m.get('Credito','')) if credito_col else m.get('Credito','')
            credito_str = str(credito_val).strip()
            credito_str = re.sub(r'[^\d,.\-]', '', credito_str)
            if credito_str.count(',') == 1 and credito_str.count('.') == 0:
                credito_norm = credito_str.replace('.', '').replace(',', '.')
            else:
                credito_norm = credito_str.replace(',', '')
            credito_num = pd.to_numeric(credito_norm, errors='coerce')
            fecha_val = m.get(fecha_col, '') if fecha_col else ''
            fecha_dt = pd.to_datetime(fecha_val, errors='coerce')

            resultados.append({
                'Fecha': fecha_dt,
                'Cuit/Cuil': cuit_raw,
                'Nombre': nombre,
                'Lote': lote,
                'Golf': golf,
                'Valor_num': credito_num,
                'Valor_raw': credito_val,
                'Concepto encontrado': m.get(concepto_col, '')
            })

        progress.progress(int((idx+1)/total_personas*100))
    progress.empty()

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty:
        st.info("No se encontraron coincidencias.")
        st.session_state['processed'] = False
        st.session_state['df_detalle_display'] = None
        st.session_state['res_sorted'] = None
    else:
        df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], errors='coerce')
        df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
        df_detalle['Fecha_str'] = df_detalle['Fecha'].apply(format_date_iso)
        df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')
        df_detalle_display = df_detalle[['Fecha_str','Cuit/Cuil','Nombre','Lote','Golf','Valor_formateado','Concepto encontrado']].copy()
        df_detalle_display = df_detalle_display.rename(columns={'Fecha_str': 'Fecha','Valor_formateado': 'Valor transferido'})

        st.subheader("Detalle de coincidencias")
        st.dataframe(df_detalle_display)

        df_resumen = df_detalle.copy()
        df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)
        resumen = df_resumen.groupby(['Cuit/Cuil','Nombre','Lote','Golf'], as_index=False)['Valor_num'].sum()
        resumen = resumen.rename(columns={'Valor_num':'Suma_total_num'})
        resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')
        resumen_display = resumen[['Cuit/Cuil','Nombre','Lote','Golf','Suma total','Suma_total_num']].copy()
        res_sorted = resumen_display.sort_values('Suma_total_num', ascending=False)

        st.subheader("Resumen (suma por Cuit/Cuil)")
        st.dataframe(res_sorted[['Cuit/Cuil','Nombre','Lote','Golf','Suma total']])

        # Guardar resultados en session_state para persistir entre reruns
        st.session_state['df_detalle_display'] = df_detalle_display
        st.session_state['res_sorted'] = res_sorted
        st.session_state['processed'] = True

    st.success("Procesamiento finalizado.")

# ---------- Buscador por Lote persistente ----------
st.markdown("---")
st.subheader("Buscar por Lote (resalta coincidencias)")

if st.button("Resetear resultados"):
    for k in ['df_detalle_display','res_sorted','processed','search_lote']:
        if k in st.session_state:
            del st.session_state[k]
    st.experimental_rerun()

if st.session_state.get('processed', False) and st.session_state['df_detalle_display'] is not None and st.session_state['res_sorted'] is not None:
    df_detalle_display = st.session_state['df_detalle_display']
    res_sorted = st.session_state['res_sorted']

    # Crear text_input usando el valor guardado en session_state; NO reasignar manualmente después
    search_lote = st.text_input(
        "Ingresá número de lote para buscar (ej: 41)",
        value=st.session_state.get('search_lote', ''),
        key="search_lote"
    )

    if search_lote:
        # No reasignar st.session_state['search_lote'] aquí; el widget actualiza session_state automáticamente
        search_lower = str(search_lote).strip().lower()
        mask_det = df_detalle_display['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
        matches_det = df_detalle_display[mask_det]
        count_det = len(matches_det)

        mask_res = res_sorted['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
        matches_res = res_sorted[mask_res]
        count_res = len(matches_res)

        st.write(f"Coincidencias en detalle: **{count_det}** — Coincidencias en resumen: **{count_res}**")

        if count_det > 0:
            def highlight_det(row):
                return ['background-color: yellow' if search_lower in str(row['Lote']).lower() else '' for _ in row.index]

            styled_det = matches_det.style.apply(highlight_det, axis=1)
            st.markdown("**Detalle (filtrado por lote)**")
            st.dataframe(styled_det)
        else:
            st.info("No se encontraron filas en el detalle para ese lote.")

        if count_res > 0:
            matches_res_display = matches_res[['Cuit/Cuil','Nombre','Lote','Golf','Suma total']].copy()

            def highlight_res(row):
                return ['background-color: yellow' if search_lower in str(row['Lote']).lower() else '' for _ in row.index]

            styled_res = matches_res_display.style.apply(highlight_res, axis=1)
            st.markdown("**Resumen (filtrado por lote)**")
            st.dataframe(styled_res)
        else:
            st.info("No se encontraron filas en el resumen para ese lote.")
else:
    st.info("Procesa archivos primero para habilitar la búsqueda por lote.")

# Mostrar versión en la interfaz
st.caption(f"Versión de la app: {VERSION}")

# Versión: 4.6