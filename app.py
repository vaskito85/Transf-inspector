import io
import re
import traceback
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode
from PIL import Image, ImageDraw

# =========================
# Config general
# =========================
st.set_page_config(page_title="Buscador CUIT - Movimientos", layout="wide", page_icon="🔎")
VERSION = "7.2.0"

# ---------- Estilos (CSS ligero) ----------
CSS = """
<style>
:root { --brand: #1976d2; --brand-2: #0d47a1; }
html, body, [class*="css"]  { font-family: "Inter", system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, "Helvetica Neue", Arial; }
section.main > div { padding-top: 1rem; }

/* Hero */
.hero {
  border-radius: 16px; padding: 20px 24px;
  background: linear-gradient(135deg, rgba(25,118,210,0.08), rgba(25,118,210,0.02) 60%), var(--background);
  border: 1px solid rgba(25,118,210,0.15);
}
.hero h1 { margin: 0; font-weight: 800; letter-spacing: -0.01em; }
.hero .chips { display:flex; gap:.5rem; flex-wrap:wrap; margin-top:.25rem }
.chip { display:inline-flex; align-items:center; gap:.4rem; border-radius: 999px; padding:.25rem .6rem; font-size:.85rem;
  background: rgba(25,118,210,.08); color: var(--brand-2); border: 1px solid rgba(25,118,210,.2); }

/* Botonera sticky */
.sticky-actions { position: sticky; top: -10px; z-index: 11; padding: .5rem 0 .8rem 0; }
.downloads { display:flex; gap:.5rem; flex-wrap:wrap; }

/* Encabezados */
h3 span.icon { font-size: 1.25rem; opacity:.9; margin-right:.3rem }

/* Ocultar footer */
footer {visibility: hidden;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---------- pequeño logo ----------
def make_logo(size=56, bg_color=(255, 255, 255, 0), circle_color=(25, 118, 210, 255)):
    img = Image.new("RGBA", (size, size), bg_color)
    draw = ImageDraw.Draw(img)
    margin = int(size * 0.12)
    draw.rounded_rectangle([0,0,size,size], radius=int(size*.22), fill=(255,255,255,255), outline=(230,230,230,255))
    draw.ellipse([margin, margin, size - margin, size - margin], fill=circle_color)
    inner = int(size * 0.30)
    draw.ellipse([size//2 - inner//2, size//2 - inner//2, size//2 + inner//2, size//2 + inner//2], fill=(255,255,255,220))
    return img

# =========================
# Session state
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
    'search_kw': '',
    'search_cuit': '',
    'kpis': {},
    'page_size': 25
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# =========================
# Utilidades de negocio
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
    s = f"{v_abs:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    return sign + s

def is_integer_string(s): return bool(re.fullmatch(r'[+-]?\d+', str(s).strip()))
def is_float_string(s):   return bool(re.fullmatch(r'[+-]?\d+\.\d+', str(s).strip()))

def safe_int_like_to_str(v):
    try:
        if pd.isna(v): return ''
        if isinstance(v, int) and not isinstance(v, bool): return str(v)
        try:
            import numpy as np
            if isinstance(v, (np.integer,)): return str(int(v))
        except:
            pass
        if hasattr(v, 'item'):
            vv = v.item()
            if isinstance(vv, int) and not isinstance(vv, bool): return str(vv)
            if isinstance(vv, float):
                if float(vv).is_integer(): return str(int(vv))
                return str(vv)
        if isinstance(v, float):
            if float(v).is_integer(): return str(int(v))
            return str(v)
        if isinstance(v, str):
            s = v.strip()
            if s == '': return ''
            if is_integer_string(s): return s
            if is_float_string(s):
                try:
                    f = float(s);  return str(int(f)) if f.is_integer() else str(f)
                except:
                    pass
            return s
        s = str(v).strip()
        if s == '': return ''
        if is_integer_string(s): return s
        if is_float_string(s):
            try:
                f = float(s); return str(int(f)) if f.is_integer() else str(f)
            except:
                pass
        return s
    except:
        return ''

def try_cast_int_series_safe(s: pd.Series) -> pd.Series:
    try:
        s2 = s.copy()
        numeric = pd.to_numeric(s2, errors='coerce')
        non_null = numeric.dropna()
        if non_null.empty: return s2
        if (non_null % 1 == 0).all(): return numeric.astype('Int64')
        return s2
    except:
        return s

def unique_join(values):
    seen, result = set(), []
    for v in values:
        vv = (str(v) if v is not None else '').strip()
        if vv and vv.lower() not in ('nan', 'none') and vv not in seen:
            seen.add(vv); result.append(vv)
    return " / ".join(result)

def find_col(df: pd.DataFrame, keywords):
    cols = [str(c) for c in df.columns]; kl = [k.lower() for k in keywords]
    for k in kl:
        for c in cols:
            if c.lower() == k: return c
    for k in kl:
        for c in cols:
            if c.lower().startswith(k): return c
    for k in kl:
        for c in cols:
            if k in c.lower(): return c
    return None

# --- Parser AR/EN de dinero (robusto, maneja negativos al final, paréntesis, CR/DB, y unicode minus) ---
def parse_money_ar(s: str):
    if s is None:
        return float('nan')
    if isinstance(s, (int, float)):
        try:
            return float(s)
        except:
            return float('nan')

    s = str(s).strip()
    if s == '':
        return float('nan')

    # Normalizaciones típicas de extractos
    s = s.replace('\u2212', '-')  # minus sign unicode
    # Quitar etiquetas texto comunes (CR/DB/cred/debe)
    s = re.sub(r'(?i)\b(cr|db|cred|debe|credito|crédito|d[eé]bito)\b', '', s)

    # Negativo al final: "1.234,56-" -> "-1.234,56"
    if re.search(r'[-–−]\s*$', s):
        s = '-' + re.sub(r'[-–−]\s*$', '', s)

    # Conservar dígitos/separadores/signos/paréntesis
    s = re.sub(r'[^\d,.\-\(\)]', '', s)

    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg = True
        s = s[1:-1]

    last_comma, last_dot = s.rfind(','), s.rfind('.')
    if last_comma == -1 and last_dot == -1:
        s_clean = re.sub(r'[,.]', '', s)
    else:
        dec = ',' if last_comma > last_dot else '.'
        idx = last_comma if dec == ',' else last_dot
        int_part = re.sub(r'[.,]', '', s[:idx]); dec_part = s[idx+1:]
        s_clean = f"{int_part}.{dec_part}"

    try:
        val = float(s_clean)
        if neg or s.strip().startswith('-'):
            val = -val
        return val
    except:
        return float('nan')

def get_credit_value_from_row(row: pd.Series, credito_col: str, df_cols: list, max_look_ahead: int = 2):
    def looks_symbolic(x: str) -> bool:
        s = str(x).strip()
        return (s == '' or s in ('$', '-', '$-', '-$') or re.fullmatch(r'[\$\-\s]+', s) is not None)
    val = row.get(credito_col, '')
    if not looks_symbolic(val) and re.search(r'\d', str(val)): return val
    try:
        j = df_cols.index(credito_col)
        for k in range(1, max_look_ahead + 1):
            if j + k < len(df_cols):
                vnext = row.get(df_cols[j + k], '')
                if not looks_symbolic(vnext) and re.search(r'\d', str(vnext)):
                    return vnext
    except:
        pass
    return val

CUIT_LEN = 11
def cuit_is_valid(cuit_digits: str) -> bool:
    if not re.fullmatch(r'\d{11}', cuit_digits or ''): return False
    digits = list(map(int, cuit_digits))
    factors = [5,4,3,2,7,6,5,4,3,2]
    s = sum(d * f for d, f in zip(digits[:10], factors))
    mod = 11 - (s % 11); check = 0 if mod == 11 else (9 if mod == 10 else mod)
    return check == digits[10]

def extract_digit_runs(s): return re.findall(r'\d{7,}', s or '')

def find_cuits_in_text(concepto_digits: str):
    """Versión usada en modo comparado; trabaja sobre solo dígitos."""
    found = set()
    if not concepto_digits: return found
    for run in extract_digit_runs(concepto_digits):
        if len(run) < CUIT_LEN: continue
        for i in range(len(run) - CUIT_LEN + 1):
            found.add(run[i:i+CUIT_LEN])
    return found

# --- Detectar CUIT/CUIL con o sin separadores (para modo banco-only) ---
CUIT_RE = re.compile(
    r'(?<!\d)'                              # no dígito antes
    r'(\d{2})\D?(\d{8})\D?(\d{1})'          # 2-8-1 con separadores opcionales
    r'(?!\d)',                              # no dígito después
    flags=re.UNICODE
)

def find_cuits_in_free_text(s: str):
    """Devuelve set de CUITs normalizados (11 dígitos) hallados en s libre."""
    if not s:
        return set()
    out = set()
    for m in CUIT_RE.finditer(str(s)):
        out.add("".join(m.groups()))
    return out

# --- Heurística simple para extraer el nombre cerca del CUIT (modo banco-only) ---
STOPWORDS_UPPER = {
    'DE','DEL','LA','LOS','LAS','A','AL','POR','PARA','EN','DA','DO','Y','E','O','U',
    'CTA','NRO','Nº','CUENTA','BANCO','TRANSFERENCIA','TRANSF','CBU','ALIAS','REF','REF.',
    'COMISION','COMISIONES','IMP','IVA','DEBITO','CR','DB','CR.','DÉBITO','CRÉDITO','PAGO','A/'
}

def guess_name_around_cuit(concepto: str, cuit_digits: str, window_tokens: int = 8):
    """
    Busca el CUIT en el concepto y toma una ventana de tokens alrededor.
    Retorna un nombre probable (string) o '' si no encuentra algo razonable.
    """
    if not concepto:
        return ''
    text = str(concepto)

    # Patrón flexible para el CUIT específico
    c = cuit_digits
    cpat = re.compile(rf'(?<!\d){c[:2]}\D*{c[2:10]}\D*{c[10]}(?!\d)')

    m = cpat.search(text)
    if not m:
        return ''

    # Tokenizar: palabras/números
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ.'/-]+|\d+", text, flags=re.UNICODE)

    # Offsets de tokens para ubicar ventana
    spans = []
    pos = 0
    for tok in tokens:
        idx = text.find(tok, pos)
        if idx == -1:
            idx = pos
        spans.append((tok, idx, idx + len(tok)))
        pos = idx + len(tok)

    center_idx = None
    for i, (_, a, b) in enumerate(spans):
        if not (b <= m.start() or a >= m.end()):
            center_idx = i
            break
    if center_idx is None:
        center_idx = max(0, min(len(tokens) - 1, len(tokens)//2))

    i0 = max(0, center_idx - window_tokens)
    i1 = min(len(tokens), center_idx + window_tokens + 1)
    window = tokens[i0:i1]

    def clean_tok(t):
        t2 = t.strip().strip(",.;:()[]{}")
        return t2

    window = [clean_tok(t) for t in window if clean_tok(t)]

    best = ''
    best_score = -1
    for n in range(2, 6):
        for k in range(0, max(0, len(window) - n + 1)):
            chunk = window[k:k+n]
            if any(t.isdigit() for t in chunk):
                continue
            chunk_norm = [t.upper() for t in chunk]
            if all(t in STOPWORDS_UPPER for t in chunk_norm):
                continue
            upper_ratio = sum(1 for t in chunk if t.isalpha() and t.isupper()) / n
            alpha_ratio = sum(1 for t in chunk if any(ch.isalpha() for ch in t)) / n
            score = upper_ratio * 1.2 + alpha_ratio
            candidate = " ".join(chunk).strip()
            candidate = re.sub(r"\b(CUIT|CUIL)\b[:#]?\s*\d+", "", candidate, flags=re.I).strip()
            candidate = re.sub(r"\bS\.?A\.?\b", "S.A.", candidate)
            if len(candidate) >= 4 and score > best_score:
                best_score = score
                best = candidate

    return best

# =========================
# Lectura segura de Excel
# =========================
@st.cache_data
def read_excel_bytes_from_buffer(buf_bytes, ext_hint=None):
    if not buf_bytes: 
        raise ValueError("Archivo vacío o no cargado.")
    buf = io.BytesIO(buf_bytes)
    ext = (ext_hint or '').lower()
    try:
        if ext == "xls":
            return pd.read_excel(buf, dtype=str, engine="xlrd").fillna('')
        else:
            return pd.read_excel(buf, dtype=str, engine="openpyxl").fillna('')
    except Exception:
        buf.seek(0)
        try:
            return pd.read_excel(buf, dtype=str).fillna('')
        except Exception as e2:
            raise ValueError(
                f"No pude leer el Excel ({ext or 'desconocido'}). "
                "Si es .XLS, asegurate de tener 'xlrd' instalado y que el archivo no esté protegido/corrupto. "
                f"Detalle: {e2}"
            )

# =========================
# Procesamiento principal (modo comparado Personas vs Banco)
# =========================
@st.cache_data(show_spinner=False)
def process_files(personas_bytes, banco_bytes, personas_name, banco_name, validate_cuit=True, show_unknown=True):
    personas = read_excel_bytes_from_buffer(personas_bytes, ext_hint=(personas_name.split('.')[-1] if personas_name else None))
    banco    = read_excel_bytes_from_buffer(banco_bytes,    ext_hint=(banco_name.split('.')[-1] if banco_name else None))

    # Sinónimos ampliados
    concepto_col = find_col(banco, ['concepto', 'concept', 'descripcion', 'descripción', 'detalle', 'detalle concepto'])
    credito_col  = find_col(banco, ['crédito', 'credito', 'credit', 'importe', 'monto', 'acreditacion', 'acreditación', 'haber'])
    fecha_col    = find_col(banco, ['fecha', 'date', 'fecha de', 'f. operación', 'f operación'])
    if not concepto_col: raise ValueError("No se encontró la columna de Concepto en el archivo bancario.")
    if not credito_col:  raise ValueError("No se encontró columna de Crédito/Importe/Monto en el archivo bancario.")

    cuit_col   = find_col(personas, ['cuit', 'cuil'])
    nombre_col = find_col(personas, ['nombre', 'name'])
    lote_col   = find_col(personas, ['lote'])
    golf_col   = find_col(personas, ['golf'])
    if not cuit_col: raise ValueError("No se encontró la columna de Cuit/Cuil en el archivo de personas.")

    personas = personas.copy(); banco = banco.copy()
    personas['cuit_raw']    = personas[cuit_col].astype(str).str.strip()
    personas['cuit_digits'] = personas['cuit_raw'].apply(only_digits)

    cuit_list = [c for c in personas['cuit_digits'].dropna().unique().tolist() if c]
    if validate_cuit: cuit_list = [c for c in cuit_list if cuit_is_valid(c)]
    cuit_set = set(cuit_list)

    if len(cuit_list) == 0 and not show_unknown:
        return pd.DataFrame(), pd.DataFrame(), {}

    banco['Concepto_str']    = banco[concepto_col].astype(str)
    banco['Concepto_digits'] = banco['Concepto_str'].str.replace(r'\D', '', regex=True)

    matches_idx, found_map, listed_map = [], {}, {}
    # Precompute cuit_raw map para fallback
    cuit_raw_map = {}
    for c_raw in personas['cuit_raw'].dropna().unique():
        rn = str(c_raw).strip().lower()
        if not rn:
            continue
        cd = only_digits(c_raw)
        if (not validate_cuit) or cuit_is_valid(cd):
            cuit_raw_map[rn] = cd

    for idx, row in banco.iterrows():
        concepto_digits = row['Concepto_digits']
        found_all = find_cuits_in_text(concepto_digits)
        if validate_cuit: found_all = {c for c in found_all if cuit_is_valid(c)}
        found_listed = set(found_all) & cuit_set
        found = set(found_all) if show_unknown else found_listed

        if not found:
            # fallback por CUIT "raw" tal cual aparece en Personas dentro del concepto
            concepto_lower = str(row.get(concepto_col, '')).lower()
            found_raw = {cd for rn, cd in cuit_raw_map.items() if rn in concepto_lower}
            found = found or found_raw
            found_listed = found & cuit_set

        if found:
            matches_idx.append(idx)
            found_map[idx] = found
            listed_map[idx] = {c: (c in cuit_set) for c in found}

    if not matches_idx: return pd.DataFrame(), pd.DataFrame(), {}
    matches = banco.loc[matches_idx].copy(); banco_cols = list(banco.columns)

    resultados = []
    for idx, m in matches.iterrows():
        concepto = str(m.get(concepto_col, ''))
        fecha_val = m.get(fecha_col, '') if fecha_col else ''
        fecha_dt = pd.to_datetime(fecha_val, dayfirst=True, errors='coerce')
        credito_val = get_credit_value_from_row(m, credito_col, banco_cols, max_look_ahead=2)
        credito_num = parse_money_ar(credito_val)

        for f in found_map.get(idx, []):
            is_listed = listed_map.get(idx, {}).get(f, False)
            p = personas[personas['cuit_digits'] == f]
            if p.empty:
                nombre = ''; lote = ''; golf = ''; cuit_display = f
            else:
                nombres_unique = [n for n in pd.unique(p[nombre_col].astype(str).str.strip())] if nombre_col else []
                lotes_unique   = [l for l in pd.unique(p[lote_col].astype(str).str.strip())]   if lote_col   else []
                golfs_unique   = [g for g in pd.unique(p[golf_col].astype(str).str.strip())]   if golf_col   else []
                nombre = unique_join(nombres_unique); lote = unique_join(lotes_unique); golf = unique_join(golfs_unique)
                cuit_raws = p['cuit_raw'].astype(str).str.strip()
                cuit_display = (pd.unique(cuit_raws)[0] if len(pd.unique(cuit_raws)) > 0 else f)

            resultados.append({
                'Fecha': fecha_dt,
                'Cuit/Cuil': cuit_display,
                'CUIT_digits': f,
                'CUIT listado': 'Sí' if is_listed else 'No',
                'Nombre': nombre,
                'Lote': lote,
                'Golf': golf,
                'Valor_num': credito_num,
                'Valor_raw': credito_val,
                'Concepto encontrado': concepto
            })

    df_detalle = pd.DataFrame(resultados)
    if df_detalle.empty: return pd.DataFrame(), pd.DataFrame(), {}

    df_detalle['Fecha'] = pd.to_datetime(df_detalle['Fecha'], dayfirst=True, errors='coerce')
    df_detalle = df_detalle.sort_values(['Cuit/Cuil', 'Fecha'])
    df_detalle['Fecha_str'] = df_detalle['Fecha'].dt.strftime('%Y-%m-%d').fillna('')
    df_detalle['Valor_formateado'] = df_detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    for _col in ['Lote', 'Golf']:
        if _col in df_detalle.columns:
            df_detalle[f"{_col}_raw"] = df_detalle[_col].astype(str).replace('nan', '').replace('None', '')
            df_detalle[f"{_col}_num"] = pd.to_numeric(df_detalle[_col].replace('', pd.NA), errors='coerce')

    cols_base = ['Fecha_str','Cuit/Cuil','CUIT_digits','CUIT listado','Nombre','Lote_raw','Golf_raw','Lote_num','Golf_num','Valor_formateado','Concepto encontrado','Valor_num']
    cols_base = [c for c in cols_base if c in df_detalle.columns]
    df_detalle_display = df_detalle[cols_base].copy().rename(columns={
        'Fecha_str': 'Fecha', 'Lote_raw': 'Lote', 'Golf_raw': 'Golf', 'Valor_formateado': 'Valor transferido'
    })

    df_resumen = df_detalle.copy()
    df_resumen['Valor_num'] = pd.to_numeric(df_resumen['Valor_num'], errors='coerce').fillna(0)
    nombres_map = df_resumen.groupby('Cuit/Cuil')['Nombre'].apply(lambda x: unique_join(x)).to_dict()

    resumen = df_resumen.groupby(['Cuit/Cuil','Lote_raw','Golf_raw'], as_index=False)['Valor_num'].sum()
    resumen['Nombre'] = resumen['Cuit/Cuil'].map(nombres_map)
    resumen = resumen.rename(columns={'Valor_num': 'Suma_total_num'})
    resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    map_lote_num = df_detalle.dropna(subset=['Lote_num']).drop_duplicates('Lote_raw').set_index('Lote_raw')['Lote_num'].to_dict()
    map_golf_num = df_detalle.dropna(subset=['Golf_num']).drop_duplicates('Golf_raw').set_index('Golf_raw')['Golf_num'].to_dict()
    resumen['Lote_num'] = resumen['Lote_raw'].map(map_lote_num)
    resumen['Golf_num'] = resumen['Golf_raw'].map(map_golf_num)

    resumen_display = resumen[['Cuit/Cuil','Nombre','Lote_raw','Golf_raw','Suma total','Suma_total_num','Lote_num','Golf_num']].copy()\
                        .rename(columns={'Lote_raw':'Lote','Golf_raw':'Golf'})

    for df_ in (df_detalle_display, resumen_display):
        if 'Lote_num' in df_.columns: df_['Lote_num'] = try_cast_int_series_safe(df_['Lote_num'])
        if 'Golf_num' in df_.columns: df_['Golf_num'] = try_cast_int_series_safe(df_['Golf_num'])

    def prefer_num_or_raw(df, col_raw, col_num):
        df = df.copy()
        if col_num in df.columns:
            col_num_obj = df[col_num].astype(object)
            col_raw_obj = df[col_raw].astype(object) if col_raw in df.columns else pd.Series([''] * len(df), index=df.index, dtype=object)
            df[col_raw] = col_num_obj.where(pd.notna(col_num_obj), col_raw_obj)
        return df

    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Lote', 'Lote_num')
    df_detalle_display = prefer_num_or_raw(df_detalle_display, 'Golf', 'Golf_num')
    resumen_display    = prefer_num_or_raw(resumen_display, 'Lote', 'Lote_num')
    resumen_display    = prefer_num_or_raw(resumen_display, 'Golf', 'Golf_num')

    res_sorted = resumen_display.sort_values('Suma_total_num', ascending=False)

    # KPIs
    kpis = {
        "Movimientos detectados": len(df_detalle_display),
        "CUITs únicos": df_detalle_display['Cuit/Cuil'].nunique() if 'Cuit/Cuil' in df_detalle_display.columns else 0,
        "Monto total": format_currency_ar(pd.to_numeric(df_detalle['Valor_num'], errors='coerce').sum()),
        "CUITs no listados": (df_detalle['CUIT listado'].eq('No')).sum() if 'CUIT listado' in df_detalle.columns else 0
    }

    return df_detalle_display.copy(), res_sorted.copy(), kpis

# =========================
# Procesamiento (modo SOLO banco)
# =========================
@st.cache_data(show_spinner=False)
def process_bank_only(banco_bytes, banco_name, validate_cuit=True, page_size=25):
    banco = read_excel_bytes_from_buffer(
        banco_bytes,
        ext_hint=(banco_name.split('.')[-1] if banco_name else None)
    )

    concepto_col = find_col(banco, ['concepto','concept','descripcion','descripción','detalle','detalle concepto'])
    credito_col  = find_col(banco, ['crédito','credito','credit','importe','monto','acreditacion','acreditación','haber'])
    fecha_col    = find_col(banco, ['fecha','date','fecha de','f. operación','f operación'])

    if not concepto_col:
        raise ValueError("No se encontró la columna de Concepto en el archivo bancario.")
    if not credito_col:
        raise ValueError("No se encontró columna de Crédito/Importe/Monto en el archivo bancario.")

    df = banco.copy()
    df['Concepto_str'] = df[concepto_col].astype(str)

    resultados = []
    banco_cols = list(df.columns)

    for idx, row in df.iterrows():
        concepto = row['Concepto_str']
        fecha_val = row.get(fecha_col, '') if fecha_col else ''
        fecha_dt = pd.to_datetime(fecha_val, dayfirst=True, errors='coerce')

        # Monto
        credito_val = get_credit_value_from_row(row, credito_col, banco_cols, max_look_ahead=2)
        credito_num = parse_money_ar(credito_val)

        # CUITs en concepto
        found = find_cuits_in_free_text(concepto)
        if validate_cuit:
            found = {c for c in found if cuit_is_valid(c)}

        for cuit_d in sorted(found):
            # Nombre/razón social detectado del texto alrededor del CUIT
            nombre_guess = guess_name_around_cuit(concepto, cuit_d)
            resultados.append({
                'Fecha': fecha_dt,
                'Cuit/Cuil': cuit_d,              # luego lo formateamos XX-XXXXXXXX-X
                'Nombre detectado': nombre_guess,
                'Valor_num': credito_num,
                'Valor_raw': credito_val,
                'Concepto': concepto
            })

    detalle = pd.DataFrame(resultados)
    if detalle.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "Movimientos detectados": 0,
            "CUITs únicos": 0,
            "Monto total": "0,00"
        }

    # Formateos
    detalle['Fecha'] = pd.to_datetime(detalle['Fecha'], dayfirst=True, errors='coerce')
    detalle['Fecha'] = detalle['Fecha'].dt.strftime('%Y-%m-%d').fillna('')
    # Formato CUIT XX-XXXXXXXX-X
    def fmt_cuit(c):
        c = str(c or '')
        return f"{c[:2]}-{c[2:10]}-{c[10:]}" if re.fullmatch(r'\d{11}', c) else c
    detalle['Cuit/Cuil'] = detalle['Cuit/Cuil'].astype(str).apply(fmt_cuit)
    detalle['Valor transferido'] = detalle['Valor_num'].apply(lambda x: format_currency_ar(x) if pd.notna(x) else '')

    # Reordenar columnas
    det_cols = ['Fecha', 'Cuit/Cuil', 'Nombre detectado', 'Valor transferido', 'Concepto', 'Valor_num']
    det_cols = [c for c in det_cols if c in detalle.columns]
    detalle = detalle[det_cols].sort_values(['Cuit/Cuil', 'Fecha'])

    # Resumen por CUIT + nombre detectado
    res = detalle.copy()
    res['Valor_num'] = pd.to_numeric(res['Valor_num'], errors='coerce').fillna(0)
    resumen = res.groupby(['Cuit/Cuil','Nombre detectado'], as_index=False)['Valor_num'].sum()
    resumen = resumen.rename(columns={'Valor_num':'Suma_total_num'})
    resumen['Suma total'] = resumen['Suma_total_num'].apply(lambda v: format_currency_ar(v))

    # KPIs
    kpis = {
        "Movimientos detectados": len(detalle),
        "CUITs únicos": detalle['Cuit/Cuil'].nunique(),
        "Monto total": format_currency_ar(pd.to_numeric(res['Valor_num'], errors='coerce').sum())
    }

    resumen_sorted = resumen.sort_values('Suma_total_num', ascending=False)
    return detalle, resumen_sorted, kpis

# =========================
# HEADER / HERO
# =========================
with st.container():
    c1, c2 = st.columns([1, 8])
    with c1: st.image(make_logo(), width=56)
    with c2:
        st.markdown(f"""
        <div class="hero">
          <h1>Buscador CUIT – Movimientos</h1>
          <div class="chips">
            <span class="chip">Versión {VERSION}</span>
            <span class="chip">Matcher rápido</span>
            <span class="chip">Parseo AR/EN</span>
            <span class="chip">Export Excel/CSV</span>
          </div>
        </div>
        """, unsafe_allow_html=True)

st.write("")

# =========================
# Helper de grilla (sin JsCode, clave única y tema streamlit)
# =========================
def show_aggrid(df, height=400, page_size=25, key="aggrid", enable_icon=False, theme="streamlit"):
    df_display = df.copy()

    # Asegurar strings en columnas textuales si existen
    for col in ['CUIT listado', 'Cuit/Cuil', 'Nombre', 'Nombre detectado', 'Lote', 'Golf', 'Fecha', 'Concepto', 'Concepto encontrado']:
        if col in df_display.columns:
            df_display[col] = df_display[col].astype(str)

    # "Iconito" robusto con emoji (no requiere HTML/JS)
    if enable_icon and 'CUIT listado' in df_display.columns:
        def to_icon(v):
            s = str(v).strip().lower()
            return '🟢 Sí' if s in ('sí', 'si', 'yes', 'y') else '🟣 No'
        df_display['CUIT listado'] = df_display['CUIT listado'].map(to_icon)

    gb = GridOptionsBuilder.from_dataframe(df_display, enableValue=True, enableRowGroup=True, enablePivot=True)
    gb.configure_default_column(filterable=True, sortable=True, resizable=True)
    gb.configure_grid_options(domLayout='normal', rowHeight=36)

    # 👉 columnas numéricas ocultas para ordenar por “valor real”
    if 'Valor_num' in df_display.columns:
        gb.configure_column('Valor_num', hide=True, type=['numericColumn'], sort='desc')
    if 'Suma_total_num' in df_display.columns:
        gb.configure_column('Suma_total_num', hide=True, type=['numericColumn'], sort='desc')

    # Paginación
    if page_size is None:
        gb.configure_grid_options(pagination=False)
    else:
        gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=page_size)

    go = gb.build()
    # Tooltips
    if 'columnDefs' in go:
        for cdef in go['columnDefs']:
            if cdef.get('field') in ('Concepto', 'Concepto encontrado', 'Nombre', 'Nombre detectado'):
                cdef['tooltipField'] = cdef['field']
    go.setdefault('enableRangeSelection', True)
    go.setdefault('clipboardDelimiter', '\t')

    AgGrid(
        df_display,
        gridOptions=go,
        height=height,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        update_mode=GridUpdateMode.NO_UPDATE,
        allow_unsafe_jscode=False,    # sin JS
        theme=theme,                  # respeta modo oscuro/claro
        key=key,
    )

# =========================
# TABS PRINCIPALES
# =========================
tab_cargar, tab_resultados, tab_buscar, tab_ajustes = st.tabs(["📤 Cargar datos", "📊 Resultados", "🔎 Buscadores", "⚙️ Ajustes"])

with tab_cargar:
    st.markdown("#### Archivos de entrada")
    col1, col2 = st.columns(2)
    with col1:
        uploaded_personas = st.file_uploader("Excel de **personas** (Cuit/Cuil, Nombre, Lote, Golf)", type=["xls","xlsx"], key="u_personas")
        if uploaded_personas is not None:
            st.session_state['uploaded_personas_bytes'] = uploaded_personas.read()
            st.session_state['uploaded_personas_name'] = getattr(uploaded_personas, "name", "")
    with col2:
        uploaded_banco = st.file_uploader("Excel de **movimientos** (Concepto, Fecha, Crédito)", type=["xls","xlsx"], key="u_banco")
        if uploaded_banco is not None:
            st.session_state['uploaded_banco_bytes'] = uploaded_banco.read()
            st.session_state['uploaded_banco_name'] = getattr(uploaded_banco, "name", "")

    st.divider()
    st.markdown("#### Parámetros de procesamiento")
    colp1, colp2, colp3 = st.columns([1.6, 1.4, 2.2])
    with colp1:
        validate_cuit = st.checkbox("Validar dígito de CUIT", value=True, help="Reduce falsos positivos en textos con números largos.")
    with colp2:
        show_unknown = st.checkbox("Incluir CUITs no listados", value=True, help="Muestra movimientos con CUIT en concepto aunque no estén en Personas.")
    with colp3:
        page_choice = st.selectbox("Tamaño de página (tablas)", options=["25","50","75","100","200","All"], index=0, key="page_choice")
        page_size = None if page_choice == "All" else int(page_choice)
        st.session_state['page_size'] = page_size

    st.write("")
    with st.form("procesar_form"):
        st.info("Pulsa **Procesar** para extraer coincidencias. Podés subir solo el Excel de banco (modo rápido).")
        submit = st.form_submit_button("🚀 Procesar")
        if submit:
            try:
                personas_ok = bool(st.session_state['uploaded_personas_bytes'])
                banco_ok    = bool(st.session_state['uploaded_banco_bytes'])

                if not banco_ok and not personas_ok:
                    st.warning("Subí al menos el Excel de **movimientos**.")
                    st.stop()

                if banco_ok and not personas_ok:
                    # --- MODO SOLO BANCO ---
                    df_detalle_display, res_sorted, kpis = process_bank_only(
                        st.session_state['uploaded_banco_bytes'],
                        st.session_state.get('uploaded_banco_name',''),
                        validate_cuit=validate_cuit,
                        page_size=st.session_state.get('page_size', 25)
                    )
                elif banco_ok and personas_ok:
                    # --- MODO COMPARADO (lógica original) ---
                    df_detalle_display, res_sorted, kpis = process_files(
                        st.session_state['uploaded_personas_bytes'],
                        st.session_state['uploaded_banco_bytes'],
                        st.session_state.get('uploaded_personas_name',''),
                        st.session_state.get('uploaded_banco_name',''),
                        validate_cuit=validate_cuit,
                        show_unknown=show_unknown
                    )
                else:
                    st.warning("Para procesar Personas necesitás también el Excel de **movimientos**.")
                    st.stop()

            except Exception as e:
                st.error(f"Error en procesamiento: {e}")
                with st.expander("Ver detalle técnico (traceback)"):
                    st.code(traceback.format_exc())
                # depuración
                try:
                    if st.session_state.get('uploaded_personas_bytes'):
                        personas_df = read_excel_bytes_from_buffer(
                            st.session_state['uploaded_personas_bytes'],
                            ext_hint=st.session_state.get('uploaded_personas_name','').split('.')[-1] if st.session_state.get('uploaded_personas_name') else None
                        )
                        st.markdown("**Primeras filas: Personas**")
                        st.dataframe(personas_df.head(8))
                    if st.session_state.get('uploaded_banco_bytes'):
                        banco_df = read_excel_bytes_from_buffer(
                            st.session_state['uploaded_banco_bytes'],
                            ext_hint=st.session_state.get('uploaded_banco_name','').split('.')[-1] if st.session_state.get('uploaded_banco_name') else None
                        )
                        st.markdown("**Primeras filas: Banco**")
                        st.dataframe(banco_df.head(8))
                except Exception as e2:
                    st.text(f"No se pudieron leer para depuración: {e2}")
                st.stop()

            if df_detalle_display.empty:
                st.info("No se encontraron coincidencias.")
                st.session_state['df_detalle_display'] = None
                st.session_state['res_sorted'] = None
                st.session_state['kpis'] = {}
                st.session_state['processed'] = False
            else:
                st.session_state['df_detalle_display'] = df_detalle_display.copy()
                st.session_state['res_sorted'] = res_sorted.copy()
                st.session_state['kpis'] = kpis
                st.session_state['processed'] = True
                st.success("Listo 🎉 Resultados guardados. Revisá la pestaña **Resultados**.")

with tab_resultados:
    st.markdown('### <span class="icon">📊</span>Vista general', unsafe_allow_html=True)
    if st.session_state.get('df_detalle_display') is None:
        st.info("No hay resultados procesados. Andá a **Cargar datos** y ejecutá el procesamiento.")
    else:
        kpis = st.session_state.get('kpis', {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Movimientos detectados", f"{kpis.get('Movimientos detectados', 0):,}".replace(',', '.'))
        c2.metric("CUITs únicos", f"{kpis.get('CUITs únicos', 0):,}".replace(',', '.'))
        c3.metric("Monto total (aprox.)", kpis.get('Monto total', '0,00'))
        if 'CUITs no listados' in kpis:
            c4.metric("CUITs no listados", f"{kpis.get('CUITs no listados', 0):,}".replace(',', '.'))
        else:
            c4.empty()

        st.write("")
        st.markdown("#### Detalle")
        df_det_show = st.session_state['df_detalle_display'].copy()

        # columnas dinámicas (soporta ambos modos)
        det_cols_candidates = [
            'Fecha', 'Cuit/Cuil',
            'CUIT listado', 'Nombre', 'Lote', 'Golf',            # modo comparado
            'Nombre detectado',                                   # modo banco-only
            'Valor transferido', 'Concepto', 'Concepto encontrado',
            'Valor_num'                                           # numérica oculta para ordenar
        ]
        det_cols = [c for c in det_cols_candidates if c in df_det_show.columns]
        show_aggrid(
            df_det_show[det_cols],
            height=420,
            page_size=st.session_state.get('page_size', 25),
            key="grid_detalle_main",
            enable_icon=True,
            theme="streamlit"
        )

        st.write("")
        st.markdown("#### Resumen")
        res_sorted_df = st.session_state['res_sorted'].copy()
        res_cols_candidates = [
            'Cuit/Cuil',
            'Nombre',           # comparado
            'Nombre detectado', # banco-only
            'Lote','Golf',      # comparado
            'Suma total', 'Suma_total_num'
        ]
        res_cols = [c for c in res_cols_candidates if c in res_sorted_df.columns]
        show_aggrid(
            res_sorted_df[res_cols],
            height=320,
            page_size=st.session_state.get('page_size', 25),
            key="grid_resumen_main",
            enable_icon=False,
            theme="streamlit"
        )

        # Descargas
        st.write("")
        st.markdown('<div class="sticky-actions downloads">', unsafe_allow_html=True)
        df_det_export = df_det_show[det_cols].copy()
        for c in ['Lote','Golf']:
            if c in df_det_export.columns: df_det_export[c] = df_det_export[c].apply(safe_int_like_to_str)
        csv_det = df_det_export.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')

        df_res_export = res_sorted_df[res_cols].copy()
        for c in ['Lote','Golf']:
            if c in df_res_export.columns: df_res_export[c] = df_res_export[c].apply(safe_int_like_to_str)
        csv_res = df_res_export.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')

        def dfs_to_excel_bytes(**sheets):
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                for name, df in sheets.items(): df.to_excel(writer, sheet_name=name, index=False)
            bio.seek(0); return bio
        excel_bytes = dfs_to_excel_bytes(Detalle=df_det_export, Resumen=df_res_export)

        cdl1, cdl2, cdl3 = st.columns([1.2, 1.2, 1.6])
        with cdl1: st.download_button("⬇️ Detalle CSV", data=csv_det, file_name="detalle.csv", mime="text/csv", use_container_width=True, key="dl_detalle_csv")
        with cdl2: st.download_button("⬇️ Resumen CSV", data=csv_res, file_name="resumen.csv", mime="text/csv", use_container_width=True, key="dl_resumen_csv")
        with cdl3: st.download_button("⬇️ Excel (Detalle + Resumen)", data=excel_bytes, file_name="resultados.xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       use_container_width=True, key="dl_excel_xlsx")

with tab_buscar:
    st.markdown('### <span class="icon">🔎</span>Buscadores', unsafe_allow_html=True)
    if st.session_state.get('df_detalle_display') is None:
        st.info("No hay resultados procesados para buscar. Procesá primero.")
    else:
        df_det = st.session_state['df_detalle_display'].copy()

        # Buscar por Lote (solo si existe)
        if 'Lote' in df_det.columns:
            with st.expander("🏷️ Buscar por Lote"):
                exact_lote = st.checkbox("Coincidencia exacta", value=False, key="exact_lote")
                search_lote = st.text_input("Número de lote (ej: 41)", value=st.session_state.get('search_lote',''), key="input_lote")
                if search_lote:
                    search_lower = str(search_lote).strip().lower()
                    mask_det = (df_det['Lote'].astype(str).str.strip().str.lower() == search_lower) if exact_lote \
                               else df_det['Lote'].astype(str).str.lower().str.contains(search_lower, na=False)
                    matches_det = df_det.loc[mask_det].copy()
                    st.write(f"Coincidencias (detalle): **{len(matches_det)}**")
                    if len(matches_det) > 0:
                        det_cols = [c for c in ['Fecha','Cuit/Cuil','CUIT listado','Nombre','Lote','Golf','Valor transferido','Concepto encontrado','Valor_num'] if c in matches_det.columns]
                        show_aggrid(
                            matches_det[det_cols], height=300, page_size=st.session_state.get('page_size', 25),
                            key=f"grid_busq_lote_{search_lower}_{int(exact_lote)}", enable_icon=True, theme="streamlit"
                        )

        # Buscar por palabra clave (Concepto / Nombre)
        with st.expander("🧠 Buscar por palabra clave (Concepto / Nombre)", expanded=True):
            search_kw = st.text_input("Palabra clave", value=st.session_state.get('search_kw',''), key="input_kw")
            if search_kw:
                kw_lower = str(search_kw).strip().lower()
                name_cols = [c for c in ['Nombre','Nombre detectado'] if c in df_det.columns]
                concept_cols = [c for c in ['Concepto encontrado','Concepto'] if c in df_det.columns]
                mask_kw = False
                for c in concept_cols + name_cols:
                    s = df_det[c].astype(str).str.lower().str.contains(kw_lower, na=False)
                    mask_kw = s if isinstance(mask_kw, bool) else (mask_kw | s)
                matches_det_kw = df_det.loc[mask_kw].copy() if not isinstance(mask_kw, bool) else df_det.iloc[0:0].copy()
                st.write(f"Coincidencias (detalle): **{len(matches_det_kw)}**")
                if len(matches_det_kw) > 0:
                    det_cols = [c for c in ['Fecha','Cuit/Cuil','CUIT listado','Nombre','Nombre detectado','Lote','Golf','Valor transferido','Concepto','Concepto encontrado','Valor_num'] if c in matches_det_kw.columns]
                    show_aggrid(
                        matches_det_kw[det_cols], height=300, page_size=st.session_state.get('page_size', 25),
                        key=f"grid_busq_kw_{kw_lower}", enable_icon=True, theme="streamlit"
                    )

        # Buscar por CUIT/CUIL
        with st.expander("🆔 Buscar por CUIT/CUIL", expanded=True):
            col_cuit_a, col_cuit_b = st.columns([2, 1])
            with col_cuit_a:
                search_cuit = st.text_input("Ingresá CUIT/CUIL (con o sin separadores)", value=st.session_state.get('search_cuit',''), key="input_cuit")
            with col_cuit_b:
                exact_cuit = st.checkbox("Coincidencia exacta", value=True, key="exact_cuit")
            if search_cuit:
                q_digits = only_digits(search_cuit)
                if q_digits == '':
                    st.warning("Ingresá al menos un dígito.")
                else:
                    det_cuit_digits = df_det['Cuit/Cuil'].astype(str).apply(only_digits)
                    mask_det_cuit = (det_cuit_digits == q_digits) if exact_cuit else det_cuit_digits.str.contains(q_digits, na=False)
                    matches_det_cuit = df_det.loc[mask_det_cuit].copy()
                    st.write(f"Coincidencias (detalle): **{len(matches_det_cuit)}**")
                    if len(matches_det_cuit) > 0:
                        det_cols = [c for c in ['Fecha','Cuit/Cuil','CUIT listado','Nombre','Nombre detectado','Lote','Golf','Valor transferido','Concepto','Concepto encontrado','Valor_num'] if c in matches_det_cuit.columns]
                        show_aggrid(
                            matches_det_cuit[det_cols], height=300, page_size=st.session_state.get('page_size', 25),
                            key=f"grid_busq_cuit_{q_digits}_{int(exact_cuit)}", enable_icon=True, theme="streamlit"
                        )

with tab_ajustes:
    st.markdown('### <span class="icon">⚙️</span>Ajustes y ayuda', unsafe_allow_html=True)
    st.markdown("""
- **Grilla:** AG Grid con `theme="streamlit"` para respetar **oscuro/claro**.
- **Iconos CUIT listado:** `🟢 Sí` / `🟣 No` (sin JS/HTML).
- **Descargas:** CSV con `;` y **BOM**; Excel con 2 hojas.
- **Orden por monto real:** columnas numéricas ocultas (`Valor_num` / `Suma_total_num`) para ordenar sin perder el formato visible.
    """, unsafe_allow_html=True)

st.caption(f"Versión de la app: {VERSION}")
