# transf-bancarias.py
import streamlit as st
import pandas as pd
import re
from io import StringIO

st.set_page_config(page_title="Buscar CUIT en movimientos bancarios", layout="wide")

st.title("Buscar CUIT en movimientos bancarios y sumar por CUIT")

# Utilidades
def find_col(df, keywords):
    """Busca la primera columna cuyo nombre contenga alguna de las keywords (case-insensitive)."""
    cols = list(df.columns)
    for k in keywords:
        for c in cols:
            if k.lower() in str(c).lower():
                return c
    return None

def only_digits(s):
    return re.sub(r'\D', '', str(s))

@st.cache_data
def read_excel_bytes(uploaded_file):
    return pd.read_excel(uploaded_file, dtype=str, engine="openpyxl").fillna('')

@st.cache_data
def compile_regex(patterns):
    if not patterns:
        return None
    combined = "|".join(patterns)
    return re.compile(combined, flags=re.IGNORECASE)

# Upload
col1, col2 = st.columns(2)
with col1:
    file_personas = st.file_uploader("Sube Excel con Cuit/Cuil, Nombre, Lote, Golf", type=["xlsx"], key="personas")
with col2:
    file_banco = st.file_uploader("Sube Excel de movimientos (Concepto, Fecha, Crédito)", type=["xlsx"], key="banco")

use_aho = st.checkbox("Usar Aho-Corasick para datasets muy grandes (opcional)", value=False)
run_button = st.button("Procesar archivos")

if file_personas and file_banco and run_button:
    try:
        personas = read_excel_bytes(file_personas)
        banco = read_excel_bytes(file_banco)
    except Exception as e:
        st.error(f"Error leyendo archivos: {e}")
        st.stop()

    # Detectar columnas en banco
    concepto_col = find_col(banco, ['concepto', 'concept'])
    credito_col = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto'])
    fecha_col = find_col(banco, ['fecha', 'date', 'fecha de'])
    if not concepto_col:
        st.error("No se encontró la columna 'Concepto' en el archivo bancario. Revisa nombres de columnas.")
        st.stop()

    # Normalizar personas
    # Buscar columna cuit en personas
    cuit_col = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col = find_col(personas, ['lote'])
    golf_col = find_col(personas, ['golf'])

    if not cuit_col:
        st.error("No se encontró columna 'Cuit/Cuil' en el archivo de personas.")
        st.stop()

    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)
    # Mapas rápidos
    digits_to_raw = dict(zip(personas['cuit_digits'], personas['cuit_raw']))
    digits_to_nombre = dict(zip(personas['cuit_digits'], personas[nombre_col] if nombre_col else ['']*len(personas)))
    digits_to_lote = dict(zip(personas['cuit_digits'], personas[lote_col] if lote_col else ['']*len(personas)))
    digits_to_golf = dict(zip(personas['cuit_digits'], personas[golf_col] if golf_col else ['']*len(personas)))

    # Preparar patrones regex (evitar colisiones, usar lookarounds para dígitos)
    patterns = []
    for d in personas['cuit_digits'].unique():
        if not d:
            continue
        # patrón que no esté dentro de otro número: (?<!\d)D(?!\d)
        patterns.append(r'(?<!\d)'+re.escape(d)+r'(?!\d)')
        # también incluir la forma con guiones si existe en raw
        raw = digits_to_raw.get(d, '')
        if raw and raw != d:
            patterns.append(r'(?<!\d)'+re.escape(raw)+r'(?!\d)')

    regex = compile_regex(patterns)

    # Opción Aho-Corasick si el usuario la selecciona
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

    # Procesar filas del banco
    resultados = []
    total_rows = len(banco)
    progress = st.progress(0)
    for i, (_, row) in enumerate(banco.iterrows()):
        concepto = str(row.get(concepto_col, ''))
        fecha_val = row.get(fecha_col, '') if fecha_col else ''
        credito_val = row.get(credito_col, '') if credito_col else ''
        # normalizar credito a número si es posible
        credito_num = pd.to_numeric(str(credito_val).replace('.','').replace(',','.'), errors='coerce')

        matches = []
        if use_aho and use_aho_success:
            # Aho-Corasick devuelve (end_index, value)
            for end_index, val in automaton.iter(concepto):
                matched_digits, matched_raw = val
                matches.append(matched_digits)
        else:
            if regex:
                found = regex.findall(concepto)
                # found puede devolver tuplas si hay grupos; asegurar lista de strings
                if found:
                    # flatten if necessary
                    flat = []
                    for f in found:
                        if isinstance(f, tuple):
                            # tomar primer elemento no vacío
                            for part in f:
                                if part:
                                    flat.append(part)
                                    break
                        else:
                            flat.append(f)
                    matches = flat

        # por cada match, normalizar a dígitos y buscar datos de persona
        for m in matches:
            matched_digits = only_digits(m)
            if not matched_digits:
                continue
            cuit_raw = digits_to_raw.get(matched_digits, m)
            nombre = digits_to_nombre.get(matched_digits, '')
            lote = digits_to_lote.get(matched_digits, '')
            golf = digits_to_golf.get(matched_digits, '')
            etiqueta = lote if str(lote).strip() else golf if str(golf).strip() else ''
            # parse fecha
            fecha_parsed = pd.to_datetime(fecha_val, errors='coerce')
            resultados.append({
                'Cuit/Cuil': cuit_raw,
                'Nombre': nombre,
                'Lote o Golf': etiqueta,
                'Fecha': fecha_parsed,
                'Valor transferido': credito_num if pd.notna(credito_num) else credito_val,
                'Concepto encontrado': concepto
            })

        # actualizar progreso
        if total_rows:
            progress.progress(int((i+1)/total_rows*100))

    progress.empty()

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty:
        st.info("No se encontraron coincidencias entre los archivos.")
    else:
        # Formatear y mostrar
        df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], errors='coerce')
        df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
        st.subheader("Detalle de coincidencias")
        st.dataframe(df_detalle)

        # Resumen por Cuit/Cuil
        df_resumen = df_detalle.copy()
        df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor transferido'], errors='coerce').fillna(0)
        resumen = df_resumen.groupby(['Cuit/Cuil', 'Nombre', 'Lote o Golf'], as_index=False)['Valor_num'].sum()
        resumen = resumen.rename(columns={'Valor_num': 'Suma total'})
        resumen = resumen.sort_values('Suma total', ascending=False)
        st.subheader("Resumen por Cuit/Cuil (suma)")
        st.dataframe(resumen)

        # Botones para descargar CSV
        csv_detalle = df_detalle.to_csv(index=False)
        csv_resumen = resumen.to_csv(index=False)
        st.download_button("Descargar detalle CSV", data=csv_detalle, file_name="detalle_coincidencias.csv", mime="text/csv")
        st.download_button("Descargar resumen CSV", data=csv_resumen, file_name="resumen_por_cuit.csv", mime="text/csv")

    st.success("Procesamiento finalizado.")