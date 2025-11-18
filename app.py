import streamlit as st
import pandas as pd
import re

st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide")
st.title("Buscar CUIT en movimientos bancarios")

# Versión de la app
VERSION = "4"

# Utilidades
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

# Upload
col1, col2 = st.columns(2)
with col1:
    file_personas = st.file_uploader("Sube Excel con Cuit/Cuil, Nombre, Lote, Golf", type=["xlsx"], key="personas")
with col2:
    file_banco = st.file_uploader("Sube Excel de movimientos (Concepto, Fecha, Crédito)", type=["xlsx"], key="banco")

run_button = st.button("Procesar archivos")

if file_personas and file_banco and run_button:
    try:
        personas = read_excel_bytes(file_personas)
        banco = read_excel_bytes(file_banco)
    except Exception as e:
        st.error(f"Error leyendo archivos: {e}")
        st.stop()

    # detectar columnas
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

    # preparar datos
    personas['cuit_raw'] = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)

    banco['Concepto_str'] = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    resultados = []
    total_personas = len(personas)
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
        etiqueta = lote if str(lote).strip() else golf if str(golf).strip() else ''

        # buscar por texto y por dígitos
        mask_text = banco['Concepto_str'].str.contains(cuit_raw, case=False, na=False, regex=False)
        mask_digits = banco['Concepto_digits'].str.contains(cuit_digits, na=False, regex=False) if cuit_digits else False
        mask = mask_text | mask_digits
        matches = banco[mask]

        for _, m in matches.iterrows():
            credito_val = m.get(credito_col, m.get('Credito','')) if credito_col else m.get('Credito','')
            # normalizar número (intentar)
            credito_num = pd.to_numeric(str(credito_val).replace('.','').replace(',','.'), errors='coerce')
            fecha_val = m.get(fecha_col, '') if fecha_col else ''
            fecha_dt = pd.to_datetime(fecha_val, errors='coerce')

            resultados.append({
                'Fecha': fecha_dt,
                'Cuit/Cuil': cuit_raw,
                'Nombre': nombre,
                'Lote': lote,
                'Golf': golf,
                'Valor transferido': credito_val,
                'Valor_num': credito_num,
                'Concepto encontrado': m.get(concepto_col, '')
            })

        if total_personas:
            progress.progress(int((idx+1)/total_personas*100))
    progress.empty()

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty:
        st.info("No se encontraron coincidencias.")
    else:
        # ordenar y mostrar detalle (Fecha primero)
        df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], errors='coerce')
        df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
        display_detalle = df_detalle[['Fecha','Cuit/Cuil','Nombre','Lote','Golf','Valor transferido','Concepto encontrado']].copy()
        st.subheader("Detalle de coincidencias")
        st.dataframe(display_detalle)

        # resumen simple: suma por Cuit/Cuil, Nombre, Lote/Golf
        df_resumen = df_detalle.copy()
        df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)
        resumen = df_resumen.groupby(['Cuit/Cuil','Nombre','Lote','Golf'], as_index=False)['Valor_num'].sum()
        resumen = resumen.rename(columns={'Valor_num':'Suma total'})
        st.subheader("Resumen (suma por Cuit/Cuil)")
        st.dataframe(resumen.sort_values('Suma total', ascending=False))

    st.success("Procesamiento finalizado.")

# Mostrar versión en la interfaz
st.caption(f"Versión de la app: {VERSION}")

# Versión: 4