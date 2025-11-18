# streamlit_app.py
import streamlit as st
import pandas as pd
import re

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
    try:
        v = float(value)
    except Exception:
        return value
    sign = '-' if v < 0 else ''
    v_abs = abs(v)
    s = f"{v_abs:,.2f}"            # 1,234,567.89
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return sign + s

def format_date_iso_nozero(d):
    if pd.isna(d):
        return ''
    try:
        return f"{d.year}-{d.month}-{d.day:02d}"
    except Exception:
        return str(d)

def agg_concepts(series):
    concepts = [str(c).strip() for c in series if pd.notna(c) and str(c).strip()!='']
    unique = []
    for c in concepts:
        if c not in unique:
            unique.append(c)
    return '; '.join(unique)

def agg_dates_iso(series):
    dates = [d for d in series if pd.notna(d)]
    if not dates:
        return ''
    unique = sorted({format_date_iso_nozero(d) for d in dates})
    return '; '.join(unique)

def agg_names(series):
    names = []
    for item in series:
        if pd.isna(item):
            continue
        # item puede ser string con saltos o lista
        if isinstance(item, list):
            for n in item:
                if n and n not in names:
                    names.append(n)
        else:
            s = str(item).strip()
            if s:
                # si contiene saltos, separar
                parts = [p.strip() for p in s.split('\n') if p.strip()]
                for p in parts:
                    if p not in names:
                        names.append(p)
    return '\n'.join(names)

def extract_names_from_concept(concept, cuit_digits):
    """Intenta extraer nombres en mayúsculas cercanos al CUIT dentro del texto del concepto."""
    if not concept or not cuit_digits:
        return []
    found_names = []
    # buscar ocurrencias del CUIT y tomar el texto que sigue (hasta 80 chars)
    for m in re.finditer(re.escape(cuit_digits), concept):
        start = m.end()
        tail = concept[start:start+80]
        # buscar secuencias en mayúsculas (nombres típicos en los ejemplos)
        caps = re.findall(r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]{1,60})', tail)
        for c in caps:
            c_clean = c.strip(' -:;')
            if len(c_clean) > 1 and c_clean not in found_names:
                found_names.append(c_clean)
    # también buscar secuencias mayúsculas en todo el concepto (por si no está justo después)
    caps_all = re.findall(r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]{2,60})', concept)
    for c in caps_all:
        c_clean = c.strip(' -:;')
        if len(c_clean) > 1 and c_clean not in found_names and cuit_digits not in c_clean:
            found_names.append(c_clean)
    return found_names

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
            fecha_parsed = pd.to_datetime(fecha_val, errors='coerce')
            # extraer nombres adicionales desde el concepto
            nombres_extra = extract_names_from_concept(concepto, matched_digits)
            resultados.append({
                'Cuit/Cuil': cuit_raw,
                'Nombre': nombre,
                'Lote': lote,
                'Golf': golf,
                'Fecha': fecha_parsed,
                'Valor transferido': credito_val,
                'Valor_num': credito_num,
                'Concepto encontrado': concepto,
                'Nombres_extra': nombres_extra
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

        # Formatear valor para mostrar
        df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

        # Fecha como string YYYY-M-DD (ejemplo 2025-1-09)
        df_detalle['Fecha_str'] = df_detalle['Fecha'].apply(format_date_iso_nozero)

        # Tabla detalle: Fecha primero
        df_detalle_display = df_detalle[['Fecha_str', 'Cuit/Cuil', 'Nombre', 'Lote', 'Golf', 'Valor_formateado', 'Concepto encontrado']].copy()
        df_detalle_display = df_detalle_display.rename(columns={
            'Fecha_str': 'Fecha',
            'Valor_formateado': 'Valor transferido'
        })

        st.subheader("Detalle de coincidencias (Fecha primero)")
        st.dataframe(df_detalle_display)

        # Resumen por Cuit/Cuil: una fila por Cuit
        resumen = df_detalle.groupby('Cuit/Cuil').agg({
            'Nombre': lambda s: '\n'.join(dict.fromkeys([x for x in s if x and str(x).strip()])),
            'Lote': lambda s: '; '.join([x for x in dict.fromkeys([str(x).strip() for x in s if x and str(x).strip()])]),
            'Golf': lambda s: '; '.join([x for x in dict.fromkeys([str(x).strip() for x in s if x and str(x).strip()])]),
            'Fecha': agg_dates_iso,
            'Concepto encontrado': agg_concepts,
            'Valor_num': 'sum',
            'Nombres_extra': agg_names
        }).reset_index()

        # Combinar nombres de personas y nombres extra (cada uno en nueva línea)
        def combine_names(row):
            persona_names = row['Nombre'].split('\n') if row['Nombre'] else []
            extra = row['Nombres_extra'].split('\n') if row['Nombres_extra'] else []
            combined = []
            for n in persona_names + extra:
                n = n.strip()
                if n and n not in combined:
                    combined.append(n)
            return '\n'.join(combined)

        resumen['Nombres'] = resumen.apply(combine_names, axis=1)
        resumen['Suma total (num)'] = resumen['Valor_num']
        resumen['Suma total'] = resumen['Suma total (num)'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

        resumen_display = resumen[['Cuit/Cuil', 'Nombres', 'Lote', 'Golf', 'Fecha', 'Concepto encontrado', 'Suma total']].copy()
        resumen_display = resumen_display.rename(columns={'Concepto encontrado': 'Conceptos', 'Fecha': 'Fechas'})

        st.subheader("Resumen por Cuit/Cuil (una fila por Cuit)")
        st.dataframe(resumen_display.sort_values('Suma total (num)', ascending=False))

        # Descargas CSV
        csv_detalle = df_detalle_display.copy()
        st.download_button("Descargar detalle CSV", data=csv_detalle.to_csv(index=False), file_name="detalle_coincidencias.csv", mime="text/csv")
        csv_resumen = resumen_display.copy()
        st.download_button("Descargar resumen CSV", data=csv_resumen.to_csv(index=False), file_name="resumen_por_cuit.csv", mime="text/csv")

    st.success("Procesamiento finalizado.")