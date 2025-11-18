# Nombre del proyecto
Transf-Inspector

## Descripción
Esta aplicación en Streamlit permite subir dos archivos Excel y buscar coincidencias de **Cuit o Cuil** del primer archivo dentro de la columna **Concepto** del archivo de movimientos bancarios. Para cada coincidencia la app extrae la **Fecha** y el **Valor transferido** y genera una tabla detallada y un resumen con la suma por Cuit.

## Características
- Subida de dos archivos Excel desde la interfaz web  
- Detección flexible de columnas Concepto Fecha y Crédito  
- Búsqueda robusta de CUIT con y sin guiones  
- Opción de usar Aho Corasick para búsquedas masivas  
- Tabla detalle con Fecha y Valor por coincidencia  
- Tabla resumen con suma total por Cuit  
- Descarga de resultados en CSV

## Requisitos
- Python 3.9 o superior  
- Paquetes listados en requirements.txt

Contenido recomendado para requirements.txt
