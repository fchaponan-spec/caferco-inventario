import io
import re
import hashlib
import hmac
from datetime import datetime

import fitz
import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="CAFERCO | Kardex e Inventario Interno",
    page_icon="📦",
    layout="wide"
)


@st.cache_resource
def cliente():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    if not url or not key:
        raise ValueError("Faltan URL o clave de Supabase en Secrets")
    return create_client(url, key)


def db():
    return cliente().schema("caferco")


def leer_todos(tabla, columnas="*", orden="", ascendente=True):
    salida = []
    inicio = 0
    tamano = 500
    while True:
        q = db().table(tabla).select(columnas)
        if orden:
            q = q.order(orden, desc=not ascendente)
        lote = q.range(inicio, inicio + tamano - 1).execute().data or []
        salida.extend(lote)
        if len(lote) < tamano:
            break
        inicio += tamano
    return salida


def productos():
    return pd.DataFrame(
        leer_todos("app_productos", orden="descripcion"),
        columns=["codigo", "descripcion", "unidad", "categoria"]
    )


def movimientos():
    return pd.DataFrame(
        leer_todos("app_movimientos", orden="id"),
        columns=[
            "id", "fecha", "codigo", "tipo",
            "cantidad", "documento", "nota"
        ]
    )


def documentos():
    return pd.DataFrame(
        leer_todos("app_documentos", orden="fecha"),
        columns=["clave", "fecha", "numero"]
    )


def stock():
    p = productos()
    m = movimientos()

    if p.empty:
        return pd.DataFrame(
            columns=[
                "codigo", "descripcion", "unidad",
                "categoria", "stock"
            ]
        )

    if m.empty:
        p["stock"] = 0.0
    else:
        m["cantidad"] = pd.to_numeric(
            m["cantidad"], errors="coerce"
        ).fillna(0)

        m["variacion"] = m["cantidad"] * m["tipo"].isin(
            ["INGRESO", "AJUSTE+"]
        ).map({True: 1, False: -1})

        totales = m.groupby(
            "codigo", as_index=False
        )["variacion"].sum()

        p = p.merge(totales, on="codigo", how="left")
        p["stock"] = p.pop("variacion").fillna(0).round(3)

    return p


def kardex():
    m = movimientos()

    if m.empty:
        return pd.DataFrame(
            columns=[
                "fecha", "codigo", "descripcion", "tipo",
                "ingreso", "salida", "saldo",
                "documento", "nota"
            ]
        )

    p = productos()[["codigo", "descripcion"]]
    m = m.merge(p, on="codigo", how="left")

    m["cantidad"] = pd.to_numeric(
        m["cantidad"], errors="coerce"
    ).fillna(0)

    m["ingreso"] = m["cantidad"].where(
        m["tipo"].isin(["INGRESO", "AJUSTE+"]), 0
    )
    m["salida"] = m["cantidad"].where(
        m["tipo"].isin(["SALIDA", "AJUSTE-"]), 0
    )

    m = m.sort_values(["fecha", "id"])

    m["saldo"] = (
        m["ingreso"] - m["salida"]
    ).groupby(m["codigo"]).cumsum().round(3)

    return m[
        [
            "fecha", "codigo", "descripcion", "tipo",
            "ingreso", "salida", "saldo",
            "documento", "nota"
        ]
    ]


def registrar_movimiento(
    codigo, tipo, cantidad, documento="", nota=""
):
    cantidad = float(cantidad)

    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero")

    if tipo not in ("INGRESO", "AJUSTE+", "AJUSTE-"):
        raise ValueError("Tipo de movimiento no permitido")

    if tipo == "AJUSTE-":
        raise ValueError(
            "Los ajustes negativos requieren una operación "
            "transaccional en la base de datos"
        )

    db().table("app_movimientos").insert({
        "codigo": codigo,
        "tipo": tipo,
        "cantidad": cantidad,
        "documento": documento,
        "nota": nota
    }).execute()


def excel(df):
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Datos")
    return b.getvalue()


def texto_pdf(archivo):
    with fitz.open(
        stream=archivo.getvalue(), filetype="pdf"
    ) as pdf:
        return "\n".join(
            pagina.get_text(sort=True) for pagina in pdf
        )


def detectar_candidatos(texto, codigos):
    encontrados = []

    for linea in texto.splitlines():
        for codigo in codigos:
            patron = (
                rf"(?<![\w-]){re.escape(str(codigo))}"
                rf"(?![\w-])\s+([0-9]+(?:[.,][0-9]+)?)"
            )
            m = re.search(patron, linea, re.IGNORECASE)

            if m:
                cantidad = float(
                    m.group(1).replace(",", ".")
                )
                if cantidad > 0:
                    encontrados.append({
                        "codigo": codigo,
                        "cantidad": cantidad
                    })

    return encontrados


def exigir_acceso():
    try:
        usuario_valido = str(
            st.secrets["auth"]["username"]
        )
        clave_valida = str(
            st.secrets["auth"]["password"]
        )
    except (KeyError, FileNotFoundError):
        st.error(
            "Configura [auth] con username y password "
            "en Streamlit Secrets."
        )
        st.stop()

    if not st.session_state.get("autenticado", False):
        st.title("🔐 CAFERCO | Acceso privado")

        with st.form("login"):
            usuario = st.text_input("Usuario")
            clave = st.text_input(
                "Contraseña", type="password"
            )
            enviar = st.form_submit_button("Ingresar")

        if enviar:
            if (
                hmac.compare_digest(usuario, usuario_valido)
                and hmac.compare_digest(clave, clave_valida)
            ):
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos")

        st.stop()

    with st.sidebar:
        st.caption("Sesión iniciada")
        if st.button("Cerrar sesión"):
            st.session_state["autenticado"] = False
            st.rerun()


exigir_acceso()

st.title("📦 CAFERCO | Kardex e Inventario Interno")
st.caption(
    "Datos en Supabase · Revisar cada salida PDF "
    "antes de descontar stock"
)

try:
    db().table("app_productos").select(
        "codigo"
    ).limit(1).execute()
except Exception as e:
    st.error(
        "No se pudo conectar al esquema caferco de Supabase. "
        "Revisa URL, clave, esquema expuesto y permisos."
    )
    st.code(str(e))
    st.stop()


tabs = st.tabs([
    "📦 Productos",
    "➕ Ingresos",
    "📄 Salidas PDF",
    "📊 Stock / Kardex",
    "🧮 Inventario físico",
    "⬇️ Respaldo"
])


with tabs[0]:
    st.subheader("Catálogo de productos")

    with st.form("nuevo_producto", clear_on_submit=True):
        c1, c2 = st.columns(2)

        codigo = c1.text_input(
            "Código único"
        ).strip().upper()

        descripcion = c2.text_input(
            "Descripción / medida"
        ).strip()

        unidad = c1.selectbox(
            "Unidad",
            ["UND", "ROLLO", "KG", "CAJA", "M", "M2"]
        )

        categoria = c2.text_input(
            "Categoría (mallas, clavos, alambres, etc.)"
        ).strip()

        guardar = st.form_submit_button("Guardar producto")

    if guardar:
        if not codigo or not descripcion:
            st.error("Ingresa código y descripción")
        else:
            try:
                db().table("app_productos").insert({
                    "codigo": codigo,
                    "descripcion": descripcion,
                    "unidad": unidad,
                    "categoria": categoria
                }).execute()

                st.success("Producto guardado")
                st.rerun()

            except Exception as e:
                st.error(
                    f"No se pudo guardar el producto: {e}"
                )

    st.dataframe(
        productos(),
        use_container_width=True,
        hide_index=True
    )


with tabs[1]:
    st.subheader("Registrar ingreso de mercadería")

    p = productos()

    if p.empty:
        st.info("Primero registra un producto")
    else:
        etiquetas = {
            r.codigo: f"{r.codigo} — {r.descripcion}"
            for r in p.itertuples()
        }

        with st.form("ingreso", clear_on_submit=True):
            codigo = st.selectbox(
                "Producto",
                list(etiquetas),
                format_func=lambda c: etiquetas[c]
            )

            cantidad = st.number_input(
                "Cantidad ingresada",
                min_value=0.001,
                value=1.0,
                step=1.0,
                format="%.3f"
            )

            documento = st.text_input(
                "Guía / documento de ingreso"
            )

            nota = st.text_input("Observación")

            enviar = st.form_submit_button(
                "Registrar ingreso"
            )

        if enviar:
            try:
                registrar_movimiento(
                    codigo,
                    "INGRESO",
                    cantidad,
                    documento,
                    nota
                )

                st.success("Ingreso registrado")
                st.rerun()

            except Exception as e:
                st.error(
                    f"No se pudo registrar: {e}"
                )


with tabs[2]:
    st.subheader(
        "Salida por guía PDF — revisión obligatoria"
    )

    st.warning(
        "La extracción del PDF puede equivocarse. "
        "Comprueba manualmente códigos y cantidades "
        "antes de confirmar."
    )

    p = productos()

    if p.empty:
        st.info(
            "Registra productos antes de procesar salidas"
        )
    else:
        archivo = st.file_uploader(
            "Adjunta la guía PDF",
            type=["pdf"]
        )

        numero = st.text_input(
            "Número de guía / documento",
            key="numero_guia"
        ).strip()

        if archivo:
            huella = hashlib.sha256(
                archivo.getvalue()
            ).hexdigest()

            st.caption(f"Archivo: {archivo.name}")

            try:
                texto = texto_pdf(archivo)

                with st.expander(
                    "Ver texto extraído del PDF"
                ):
                    st.text(
                        texto[:15000]
                        or "PDF sin texto seleccionable"
                    )

                if st.session_state.get("pdf_hash") != huella:
                    st.session_state["pdf_hash"] = huella

                    st.session_state["lineas_pdf"] = (
                        pd.DataFrame(
                            detectar_candidatos(
                                texto,
                                p["codigo"].tolist()
                            ),
                            columns=["codigo", "cantidad"]
                        )
                    )

            except Exception as e:
                st.error(
                    f"No se pudo leer el PDF: {e}"
                )
                st.stop()

            st.caption(
                "Puedes corregir, agregar o eliminar filas."
            )

            editadas = st.data_editor(
                st.session_state["lineas_pdf"],
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "codigo": st.column_config.SelectboxColumn(
                        "Código",
                        options=p["codigo"].tolist(),
                        required=True
                    ),
                    "cantidad": st.column_config.NumberColumn(
                        "Cantidad",
                        min_value=0.001,
                        format="%.3f",
                        required=True
                    )
                },
                key=f"editor_pdf_{huella}"
            )

            st.subheader("Stock disponible")

            st.dataframe(
                stock(),
                use_container_width=True,
                hide_index=True
            )

            confirmar = st.checkbox(
                "He revisado el PDF, los códigos, "
                "las cantidades y el número de guía"
            )

            if st.button(
                "Confirmar salida y descontar stock",
                type="primary",
                disabled=not confirmar
            ):
                try:
                    if not numero:
                        raise ValueError(
                            "Escribe el número de guía"
                        )

                    if editadas.empty:
                        raise ValueError(
                            "Agrega al menos una línea"
                        )

                    if editadas[
                        ["codigo", "cantidad"]
                    ].isna().any().any():
                        raise ValueError(
                            "Hay códigos o cantidades vacíos"
                        )

                    lineas = []
                    codigos_validos = set(p["codigo"])

                    for fila in editadas.itertuples(
                        index=False
                    ):
                        if (
                            fila.codigo not in codigos_validos
                            or float(fila.cantidad) <= 0
                        ):
                            raise ValueError(
                                "Código o cantidad inválidos"
                            )

                        lineas.append({
                            "codigo": str(fila.codigo),
                            "cantidad": float(fila.cantidad)
                        })

                    db().rpc(
                        "app_registrar_salida",
                        {
                            "p_clave": huella,
                            "p_numero": numero,
                            "p_archivo": archivo.name,
                            "p_lineas": lineas
                        }
                    ).execute()

                    st.success(
                        "Salida registrada y stock "
                        "descontado correctamente"
                    )

                    st.session_state.pop(
                        "pdf_hash", None
                    )
                    st.session_state.pop(
                        "lineas_pdf", None
                    )

                except Exception as e:
                    st.error(
                        f"No se registró la salida: {e}"
                    )


with tabs[3]:
    st.subheader("Stock actualizado")

    s = stock()

    st.dataframe(
        s,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Descargar stock Excel",
        excel(s),
        "stock_caferco.xlsx"
    )

    st.subheader("Kardex histórico")

    k = kardex()

    if not k.empty:
        opciones = ["TODOS"] + sorted(
            k["codigo"].dropna().unique().tolist()
        )

        elegido = st.selectbox(
            "Filtrar por producto",
            opciones
        )

        if elegido != "TODOS":
            k = k[k["codigo"] == elegido]

    st.dataframe(
        k,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Descargar Kardex Excel",
        excel(k),
        "kardex_caferco.xlsx"
    )


with tabs[4]:
    st.subheader("Conteo físico y diferencias")

    s = stock()

    if s.empty:
        st.info("Todavía no hay productos")
    else:
        fisico = s.copy()
        fisico["conteo_fisico"] = 0.0

        editado = st.data_editor(
            fisico,
            use_container_width=True,
            hide_index=True,
            disabled=[
                "codigo", "descripcion",
                "unidad", "categoria", "stock"
            ],
            column_config={
                "conteo_fisico": st.column_config.NumberColumn(
                    "Conteo físico",
                    min_value=0.0,
                    format="%.3f"
                )
            }
        )

        editado["diferencia"] = (
            pd.to_numeric(
                editado["conteo_fisico"],
                errors="coerce"
            ).fillna(0)
            -
            pd.to_numeric(
                editado["stock"],
                errors="coerce"
            ).fillna(0)
        )

        st.dataframe(
            editado,
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "Descargar comparación Excel",
            excel(editado),
            "inventario_fisico.xlsx"
        )

        st.info(
            "Los conteos no cambian el stock. "
            "Los ajustes requieren autorización "
            "y un procedimiento seguro separado."
        )


with tabs[5]:
    st.subheader("Copias de seguridad")

    st.info(
        "Descarga periódicamente estos Excel. "
        "No sustituyen un respaldo completo de Supabase."
    )

    st.download_button(
        "Descargar productos Excel",
        excel(productos()),
        "productos_caferco.xlsx"
    )

    st.download_button(
        "Descargar movimientos Excel",
        excel(movimientos()),
        "movimientos_caferco.xlsx"
    )

    st.download_button(
        "Descargar documentos Excel",
        excel(documentos()),
        "documentos_caferco.xlsx"
    )
