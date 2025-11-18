# streamlit_app.py
import streamlit as st
import pandas as pd
import re
from io import StringIO

st.set_page_config(page_title="Buscar CUIT en movimientos bancarios", layout="wide")
st.title("Buscar CUIT en movimientos bancarios y sumar por CUIT")

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
    return pd.read_excel(uploaded_file, dtype=str, engine="openpyxl").fillna('')

def format_currency_ar(value):
    """Formatea número al estilo argentino: miles con punto y decimales con coma (2 decimales)."""
    try:
        v = float(value)
    except Exception:
        return value
    sign = '-' if v < 0 else ''
    v_abs = abs(v)
    s = f"{v_abs:,.2f}"            # 1,234,567.89
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return sign + s

def agg_dates(series):
    dates = [d for d in series if pd.notna(d)]
    if not dates:
        return ''
    # formatear y ordenar únicos
    dates_str = sorted({d.strftime('%d/%m/%Y') for d in dates})
    return '; '.join(dates_str)

def agg_concepts(series):
    concepts = [str(c).strip() for c in series if pd.notna(c) and str(c).strip()!='']
    unique = []
    for c in concepts:
        if c not in unique:
            unique.append(c)
    return '; '.join(unique)

# ---------- UI ----------
col1, col2 = st.columns(2)
with col1:
    file_personas = st.file_uploader("Sube Excel con Cuit/Cuil, Nombre, Lote, Golf", type=["xlsx"], key="personas")
with col2:
    file_banco = st.file_uploader("Sube Excel de movimientos (Concepto, Fecha, Crédito)", type=["xlsx"], key="banco")

use_aho = st.checkbox("Usar Aho-Corasick para datasets muy grandes (opcional)", value=False)
run_button = st.button("Procesar archivos")

# ---------- procesamiento ----------
if file_personas and file_banco and run_button:
    try:
        personas = read_excel_bytes(file_personas)
        banco = read_excel_bytes(file_banco)
    except Exception as e:
        st.error(f"Error leyendo archivos: {e}")
        st.stop()

    concepto_col = find_col(banco, ['concepto', 'concept'])
    credito_col = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto'])
    fecha_col = find_col(banco, ['fecha', 'date', 'fecha de'])
    if not concepto_col:
        st.error("No se encontró la columna 'Concepto' en el archivo bancario. Revisa nombres de columnas.")
        st.stop()

    cuit_col = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col = find_col(personas, ['lote'])
    golf_col = find_col(personas, ['golf'])

    if not cuit_col:
        st.error("No se encontró columna 'Cuit/Cuil' en el archivo de personas.")
        st.stop()

    # normalizar personas
    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)

    digits_to_raw = dict(zip(personas['cuit_digits'], personas['cuit_raw']))
    digits_to_nombre = dict(zip(personas['cuit_digits'], personas[nombre_col] if nombre_col else ['']*len(personas)))
    digits_to_lote = dict(zip(personas['cuit_digits'], personas[lote_col] if lote_col else ['']*len(personas)))
    digits_to_golf = dict(zip(personas['cuit_digits'], personas[golf_col] if golf_col else ['']*len(personas)))

    # preparar patrones regex
    patterns = []
    for d in personas['cuit_digits'].unique():
        if not d:
            continue
        patterns.append(r'(?<!\d)'+re.escape(d)+r'(?!\d)')
        raw = digits_to_raw.get(d, '')
        if raw and raw != d:
            patterns.append(r'(?<!\d)'+re.escape(raw)+r'(?!\d)')
    regex = re.compile("|".join(patterns), flags=re.IGNORECASE) if patterns else None

    # Aho-Corasick opcional
    use_aho_success = False
    automaton = None
    if use_aho:
        try:
            import ahocorasick
            automaton = ahocorasick.Automaton()
            for d in personas['cuit_digits'].unique():
                if not d:
                    continue
                automaton.add_word(d, (d, digits_to_raw.get(d, d)))
                raw = digits_to_raw.get(d, '')
                if raw and raw != d:
                    automaton.add_word(raw, (d, digits_to_raw.get(d, d)))
            automaton.make_automaton()
            use_aho_success = True
        except Exception:
            use_aho_success = False

    resultados = []
    total_rows = len(banco)
    progress = st.progress(0)
    for i, (_, row) in enumerate(banco.iterrows()):
        concepto = str(row.get(concepto_col, ''))
        fecha_val = row.get(fecha_col, '') if fecha_col else ''
        credito_val = row.get(credito_col, '') if credito_col else ''
        # normalizar credito a número (usar punto decimal)
        credito_num = pd.to_numeric(str(credito_val).replace('.','').replace(',','.'), errors='coerce')

        matches = []
        if use_aho and use_aho_success:
            for end_index, val in automaton.iter(concepto):
                matched_digits, matched_raw = val
                matches.append(matched_digits)
        else:
            if regex:
                found = regex.findall(concepto)
                if found:
                    flat = []
                    for f in found:
                        if isinstance(f, tuple):
                            for part in f:
                                if part:
                                    flat.append(part)
                                    break
                        else:
                            flat.append(f)
                    matches = flat

        for m in matches:
            matched_digits = only_digits(m)
            if not matched_digits:
                continue
            cuit_raw = digits_to_raw.get(matched_digits, m)
            nombre = digits_to_nombre.get(matched_digits, '')
            lote = digits_to_lote.get(matched_digits, '')
            golf = digits_to_golf.get(matched_digits, '')
            etiqueta = lote if str(lote).strip() else golf if str(golf).strip() else ''
            fecha_parsed = pd.to_datetime(fecha_val, errors='coerce')
            resultados.append({
                'Cuit/Cuil': cuit_raw,
                'Nombre': nombre,
                'Lote o Golf': etiqueta,
                'Fecha': fecha_parsed,
                'Valor transferido': credito_val,
                'Valor_num': credito_num,
                'Concepto encontrado': concepto
            })

        if total_rows:
            progress.progress(int((i+1)/total_rows*100))
    progress.empty()

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty:
        st.info("No se encontraron coincidencias entre los archivos.")
    else:
        # Formateos y orden
        df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], errors='coerce')
        df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])

        # Columna de visualización del valor con formato argentino
        def valor_display(row):
            if pd.notna(row['Valor_num']):
                return format_currency_ar(row['Valor_num'])
            else:
                return str(row['Valor transferido'])
        df_detalle['Valor_formateado'] = df_detalle.apply(valor_display, axis=1)

        # Tabla detalle: Fecha primero
        df_detalle_display = df_detalle[['Fecha', 'Cuit/Cuil', 'Nombre', 'Lote o Golf', 'Valor_formateado', 'Concepto encontrado']].copy()
        df_detalle_display = df_detalle_display.rename(columns={'Valor_formateado': 'Valor transferido'})

        st.subheader("Detalle de coincidencias (Fecha primero)")
        st.dataframe(df_detalle_display)

        # Resumen por Cuit/Cuil: una fila por Cuit, listar conceptos y fechas, sumar valores
        resumen = df_detalle.groupby(['Cuit/Cuil', 'Nombre', 'Lote o Golf'], as_index=False).agg({
            'Fecha': agg_dates,
            'Concepto encontrado': agg_concepts,
            'Valor_num': 'sum'
        })
        resumen = resumen.rename(columns={
            'Fecha': 'Fechas',
            'Concepto encontrado': 'Conceptos',
            'Valor_num': 'Suma total (num)'
        })
        # Formatear suma para mostrar
        resumen['Suma total'] = resumen['Suma total (num)'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')
        resumen_display = resumen[['Cuit/Cuil', 'Nombre', 'Lote o Golf', 'Fechas', 'Conceptos', 'Suma total']]

        st.subheader("Resumen por Cuit/Cuil (una fila por Cuit)")
        st.dataframe(resumen_display.sort_values('Suma total (num)', ascending=False))

        # Descargas CSV (opcional)
        csv_detalle = df_detalle_display.copy()
        # convertir Fecha a dd/mm/YYYY para CSV
        csv_detalle['Fecha'] = csv_detalle['Fecha'].dt.strftime('%d/%m/%Y')
        st.download_button("Descargar detalle CSV", data=csv_detalle.to_csv(index=False), file_name="detalle_coincidencias.csv", mime="text/csv")

        csv_resumen = resumen_display.copy()
        st.download_button("Descargar resumen CSV", data=csv_resumen.to_csv(index=False), file_name="resumen_por_cuit.csv", mime="text/csv")

    st.success("Procesamiento finalizado.")