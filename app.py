import io
import re
import hashlib
import hmac
from datetime import datetime
import unicodedata

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


def historial_tipo(tipo):
    m = movimientos()
    if m.empty:
        return pd.DataFrame(columns=["id", "fecha", "codigo", "descripcion", "tipo", "cantidad", "documento", "nota"])
    m = m[m["tipo"] == tipo].copy()
    p = productos()
    if not p.empty:
        m = m.merge(p[["codigo", "descripcion"]], on="codigo", how="left")
    else:
        m["descripcion"] = ""
    m["cantidad"] = pd.to_numeric(m["cantidad"], errors="coerce")
    return m[["id", "fecha", "codigo", "descripcion", "tipo", "cantidad", "documento", "nota"]].sort_values("id", ascending=False)


def panel_historial(tipo, prefijo):
    st.divider()
    st.subheader("Historial de " + ("ingresos" if tipo == "INGRESO" else "salidas"))
    h = historial_tipo(tipo)
    if h.empty:
        st.info("Todavía no hay movimientos de este tipo.")
        return
    codigos = ["TODOS"] + sorted(h["codigo"].dropna().astype(str).unique().tolist())
    nombres = dict(zip(h["codigo"].astype(str), h["descripcion"].fillna(h["codigo"]).astype(str)))
    filtro = st.selectbox("Filtrar por producto", codigos,
        format_func=lambda c: "TODOS LOS PRODUCTOS" if c == "TODOS" else nombres.get(str(c), str(c)),
        key=f"{prefijo}_filtro")
    if filtro != "TODOS":
        h = h[h["codigo"] == filtro]
    st.dataframe(h, use_container_width=True, hide_index=True)
    st.download_button(
        "📥 Descargar solo " + ("ingresos" if tipo == "INGRESO" else "salidas") + " en Excel",
        excel(h),
        f"caferco_{prefijo}_{filtro.lower()}.xlsx",
        key=f"{prefijo}_descargar"
    )
    if h.empty:
        return
    st.markdown("**Editar o eliminar un movimiento**")
    opciones = {
        int(r.id): f"ID {int(r.id)} | {r.descripcion or r.codigo} | {r.cantidad:g} | {r.documento or 'Sin documento'}"
        for r in h.itertuples(index=False)
    }
    elegido = st.selectbox("Selecciona el movimiento", list(opciones),
                           format_func=lambda x: opciones[x], key=f"{prefijo}_mov")
    fila = h[h["id"] == elegido].iloc[0]
    with st.form(f"{prefijo}_modificar"):
        accion = st.radio("Acción", ["EDITAR", "ELIMINAR"], horizontal=True)
        cantidad = st.number_input("Nueva cantidad (para editar)", min_value=0.001,
                                   value=float(fila["cantidad"]), step=1.0,
                                   format="%.3f")
        motivo = st.text_input("Motivo de la corrección (obligatorio)")
        confirmar = st.checkbox("Confirmo que revisé el movimiento y su efecto en el stock")
        enviar = st.form_submit_button("Guardar corrección", type="primary")
    if enviar:
        if not confirmar:
            st.error("Marca la casilla de confirmación.")
        elif len(motivo.strip()) < 3:
            st.error("Escribe un motivo de al menos 3 caracteres.")
        else:
            try:
                db().rpc("app_modificar_movimiento", {
                    "p_id": int(elegido),
                    "p_accion": accion,
                    "p_cantidad": float(cantidad) if accion == "EDITAR" else None,
                    "p_motivo": motivo.strip()
                }).execute()
                st.success("Movimiento actualizado. El stock y el Kardex se recalcularán.")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo modificar el movimiento: {e}")


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


def normalizar_nombre(valor):
    texto = unicodedata.normalize("NFKD", str(valor).upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("×", "X").replace("½", "1/2")
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return " ".join(texto.split())


def detectar_candidatos_por_nombre(texto, catalogo, archivo=None):
    """Lee renglones de nota de venta CAFERCO usando columnas del PDF.

    Los codigos impresos son pistas de verificacion, no son necesarios
    para identificar por nombre. No inventa productos faltantes.
    """
    encontrados, pendientes = [], []
    if archivo is None:
        return encontrados, ["Se requiere el PDF para leer las columnas de la guía"]

    catalogo = catalogo.copy()
    catalogo["clave_nombre"] = catalogo["descripcion"].map(normalizar_nombre)

    def nombre_equivalente(x):
        x = normalizar_nombre(x)
        # Normaliza fracciones y espaciados sin eliminar el atributo M/C.
        x = re.sub(r"(?<![0-9])2\s+1\s+2(?![0-9])", "2 1 2", x)
        return x

    with fitz.open(stream=archivo.getvalue(), filetype="pdf") as doc:
        for pagina in doc:
            bloques = pagina.get_text("blocks")
            # La primera columna del detalle comienza con ITEM y CODIGO;
            # el texto del producto puede ocupar varias lineas.
            detalles = []
            cantidades = []
            for bloque in bloques:
                x0, y0, x1, y1, contenido = bloque[:5]
                if y0 < 100 or "TOTAL" in contenido.upper():
                    continue
                if x0 < pagina.rect.width * .60:
                    match = re.match(r"^\s*(\d{1,3})\s*\n\s*([A-Z0-9-]{2,})\s*\n(.+)$", contenido, re.S)
                    if match and "MALLA" in contenido.upper():
                        descripcion = " ".join(match.group(3).split())
                        detalles.append((y0, match.group(2), descripcion))
                else:
                    cantidad = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(ROLLO|UND|KG|CAJA|PAQ|BOLSA)\b", contenido.upper())
                    if cantidad:
                        cantidades.append((y0, float(cantidad.group(1).replace(",", ".")), cantidad.group(2)))
            usados = set()
            for y, codigo_impreso, descripcion in detalles:
                opciones = [(i, abs(cy-y), cantidad, unidad) for i,(cy,cantidad,unidad) in enumerate(cantidades) if i not in usados and abs(cy-y) < 12]
                if not opciones:
                    pendientes.append(f"{descripcion} | Cantidad no identificada")
                    continue
                i, _, cantidad, unidad = min(opciones, key=lambda z:z[1])
                usados.add(i)
                clave = nombre_equivalente(descripcion)
                coincidencias = catalogo[catalogo["clave_nombre"].map(nombre_equivalente) == clave]
                if len(coincidencias) == 1:
                    producto = coincidencias.iloc[0]
                    # Si existe un codigo impreso, verificar consistencia.
                    # Un cambio de codigo en el catalogo es posible; no se exige igualdad.
                    if str(producto["unidad"]).upper() != unidad:
                        pendientes.append(f"{descripcion} | {cantidad:g} {unidad} | Unidad distinta en catálogo")
                    else:
                        encontrados.append({"nombre": str(producto["descripcion"]), "cantidad": cantidad})
                elif len(coincidencias) > 1:
                    pendientes.append(f"{descripcion} | {cantidad:g} {unidad} | Nombre duplicado en catálogo")
                else:
                    pendientes.append(f"{descripcion} | {cantidad:g} {unidad} | No existe en catálogo (código de guía {codigo_impreso})")
    return encontrados, pendientes


def mostrar_panel():
    st.markdown("## 📊 Dashboard de inventario CAFERCO")
    st.caption("Indicadores calculados con los registros actuales de Supabase. Los movimientos se comparan por unidad de medida.")

    p = productos().copy()
    m = movimientos().copy()
    s = stock().copy()

    if p.empty:
        st.info("Registra productos para visualizar el dashboard.")
        return

    if not m.empty:
        m["cantidad"] = pd.to_numeric(m["cantidad"], errors="coerce").fillna(0)
        m["fecha_dt"] = pd.to_datetime(m["fecha"], errors="coerce", utc=True)
        m = m.merge(
            p[["codigo", "descripcion", "categoria", "unidad"]],
            on="codigo", how="left"
        )
        m["descripcion"] = m["descripcion"].fillna(m["codigo"])
        m["categoria"] = m["categoria"].fillna("SIN CATEGORÍA")
        m["unidad"] = m["unidad"].fillna("SIN UNIDAD")

    hoy = pd.Timestamp.now(tz="UTC")
    st.markdown("#### Filtros del dashboard")
    f1, f2, f3 = st.columns(3)
    with f1:
        periodo = st.selectbox(
            "Periodo",
            ["Últimos 30 días", "Este mes", "Últimos 90 días", "Todo el historial"],
            key="dash_periodo"
        )
    with f2:
        categorias = sorted(p["categoria"].fillna("SIN CATEGORÍA").astype(str).unique())
        categoria = st.selectbox("Categoría", ["TODAS"] + categorias, key="dash_categoria")
    with f3:
        unidades = sorted(p["unidad"].fillna("SIN UNIDAD").astype(str).unique())
        unidad = st.selectbox("Unidad de medida", unidades, key="dash_unidad")

    p_filtrados = p[p["unidad"].fillna("SIN UNIDAD") == unidad].copy()
    if categoria != "TODAS":
        p_filtrados = p_filtrados[
            p_filtrados["categoria"].fillna("SIN CATEGORÍA") == categoria
        ]
    codigos = set(p_filtrados["codigo"])
    s_filtrado = s[s["codigo"].isin(codigos)].copy()

    if m.empty:
        vista = m.copy()
    else:
        vista = m[m["codigo"].isin(codigos)].copy()
        if periodo == "Últimos 30 días":
            vista = vista[vista["fecha_dt"] >= hoy - pd.Timedelta(days=30)]
        elif periodo == "Últimos 90 días":
            vista = vista[vista["fecha_dt"] >= hoy - pd.Timedelta(days=90)]
        elif periodo == "Este mes":
            vista = vista[
                (vista["fecha_dt"].dt.year == hoy.year) &
                (vista["fecha_dt"].dt.month == hoy.month)
            ]

    entradas = ["INGRESO", "AJUSTE+"]
    salidas = ["SALIDA", "AJUSTE-"]
    cantidad_ingresos = float(vista.loc[vista["tipo"].isin(entradas), "cantidad"].sum()) if not vista.empty else 0
    cantidad_salidas = float(vista.loc[vista["tipo"].isin(salidas), "cantidad"].sum()) if not vista.empty else 0
    cantidad_stock = float(pd.to_numeric(s_filtrado["stock"], errors="coerce").fillna(0).sum()) if not s_filtrado.empty else 0
    productos_movidos = int(vista["codigo"].nunique()) if not vista.empty else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Stock actual", f"{cantidad_stock:,.2f} {unidad}")
    k2.metric("Ingresos del periodo", f"{cantidad_ingresos:,.2f} {unidad}")
    k3.metric("Salidas del periodo", f"{cantidad_salidas:,.2f} {unidad}")
    k4.metric("Productos con movimiento", f"{productos_movidos:,}")
    st.caption(
        "El stock es el saldo actual, mientras que ingresos y salidas corresponden al periodo elegido. "
        "Los ajustes se incluyen en sus respectivas direcciones."
    )

    t1, t2, t3 = st.tabs([
        "🏆 Productos más movidos",
        "📈 Tendencia de movimientos",
        "📦 Stock y alertas"
    ])

    with t1:
        st.subheader("Ranking de productos con mayor movimiento")
        st.caption("Movimiento total = entradas + salidas, agrupadas por producto y en la misma unidad.")
        if vista.empty:
            st.info("No existen movimientos para estos filtros.")
        else:
            datos = vista[vista["tipo"].isin(entradas + salidas)].copy()
            datos["Entradas"] = datos["cantidad"].where(datos["tipo"].isin(entradas), 0)
            datos["Salidas"] = datos["cantidad"].where(datos["tipo"].isin(salidas), 0)
            ranking = datos.groupby(["codigo", "descripcion"], as_index=False)[["Entradas", "Salidas"]].sum()
            ranking["Movimiento total"] = ranking["Entradas"] + ranking["Salidas"]
            ranking = ranking.sort_values("Movimiento total", ascending=False)
            top = ranking.head(10).copy()
            top["Producto"] = top["descripcion"] + " (" + top["codigo"].astype(str) + ")"
            st.bar_chart(
                top.set_index("Producto")[["Entradas", "Salidas"]],
                horizontal=True,
                use_container_width=True
            )
            st.dataframe(
                ranking.rename(columns={"descripcion": "Producto", "codigo": "Código"}),
                use_container_width=True,
                hide_index=True
            )
            st.download_button(
                "⬇️ Descargar ranking Excel",
                excel(ranking),
                file_name="caferco_ranking_productos.xlsx",
                key="dash_ranking_excel"
            )

    with t2:
        st.subheader("Evolución de ingresos y salidas")
        if vista.empty or vista["fecha_dt"].isna().all():
            st.info("No hay movimientos con fechas válidas para mostrar.")
        else:
            datos = vista[vista["tipo"].isin(entradas + salidas)].copy()
            datos = datos.dropna(subset=["fecha_dt"])
            if datos.empty:
                st.info("No hay movimientos para estos filtros.")
            else:
                datos["Día"] = datos["fecha_dt"].dt.date
                datos["Ingresos"] = datos["cantidad"].where(datos["tipo"].isin(entradas), 0)
                datos["Salidas"] = datos["cantidad"].where(datos["tipo"].isin(salidas), 0)
                diario = datos.groupby("Día", as_index=False)[["Ingresos", "Salidas"]].sum()
                st.line_chart(diario.set_index("Día"), use_container_width=True)
                st.download_button(
                    "⬇️ Descargar tendencia Excel",
                    excel(diario),
                    file_name="caferco_tendencia_movimientos.xlsx",
                    key="dash_tendencia_excel"
                )

    with t3:
        st.subheader("Estado del stock por producto")
        if s_filtrado.empty:
            st.info("No hay productos en esta selección.")
        else:
            s_filtrado["stock"] = pd.to_numeric(
                s_filtrado["stock"], errors="coerce"
            ).fillna(0)
            sin_stock = int((s_filtrado["stock"] <= 0).sum())
            st.metric("Productos sin stock o con saldo negativo", sin_stock)
            st.dataframe(
                s_filtrado.sort_values("stock"),
                use_container_width=True,
                hide_index=True
            )
            st.caption(
                "Las alertas de stock mínimo configurables se pueden añadir después; "
                "por ahora se señalan productos con saldo cero o negativo."
            )
            st.download_button(
                "⬇️ Descargar stock filtrado Excel",
                excel(s_filtrado),
                file_name="caferco_stock_dashboard.xlsx",
                key="dash_stock_excel"
            )

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
    "📈 Panel",
    "📦 Productos",
    "➕ Ingresos",
    "📄 Salidas PDF",
    "📊 Stock / Kardex",
    "🧮 Inventario físico",
    "⬇️ Respaldo"
])


with tabs[0]:
    mostrar_panel()


with tabs[1]:
    st.subheader("Catálogo de productos")
    st.caption("Registra, edita o elimina productos. El código no se modifica para proteger el Kardex.")

    with st.expander("➕ Agregar producto nuevo", expanded=False):
        with st.form("nuevo_producto", clear_on_submit=True):
            c1, c2 = st.columns(2)
            codigo = c1.text_input("Código único").strip().upper()
            descripcion = c2.text_input("Descripción / medida").strip()
            unidad = c1.selectbox("Unidad", ["UND", "ROLLO", "KG", "CAJA", "M", "M2"])
            categoria = c2.text_input("Categoría (mallas, clavos, alambres, etc.)").strip()
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
                    st.error(f"No se pudo guardar el producto: {e}")

    p_catalogo = productos()
    st.dataframe(p_catalogo, use_container_width=True, hide_index=True)

    if not p_catalogo.empty:
        etiquetas_producto = {
            str(r.codigo): f"{r.descripcion} — {r.codigo}"
            for r in p_catalogo.itertuples()
        }
        st.divider()
        st.subheader("Administrar productos existentes")
        producto_seleccionado = st.selectbox(
            "Selecciona el producto que deseas modificar o eliminar",
            list(etiquetas_producto),
            format_func=lambda c: etiquetas_producto[c],
            key="admin_producto_codigo"
        )
        actual = p_catalogo[p_catalogo["codigo"] == producto_seleccionado].iloc[0]
        pesta_editar, pesta_eliminar = st.tabs(["✏️ Editar producto", "🗑️ Eliminar producto"])

        with pesta_editar:
            with st.form("editar_producto_form"):
                codigo_nuevo = st.text_input(
                    "Código del producto (editable)", value=str(actual["codigo"]),
                    help="Si cambias el código, los movimientos y conteos asociados deben conservarse. Ejecuta primero el SQL adjunto."
                ).strip()
                descripcion_nueva = st.text_input(
                    "Descripción / medida", value=str(actual["descripcion"])
                ).strip()
                unidades_disponibles = ["UND", "ROLLO", "KG", "CAJA", "M", "M2"]
                unidad_actual = str(actual["unidad"])
                if unidad_actual not in unidades_disponibles:
                    unidades_disponibles.append(unidad_actual)
                unidad_nueva = st.selectbox(
                    "Unidad", unidades_disponibles,
                    index=unidades_disponibles.index(unidad_actual)
                )
                categoria_nueva = st.text_input(
                    "Categoría", value=str(actual["categoria"])
                ).strip()
                confirmar_edicion = st.form_submit_button("💾 Guardar cambios")
            if confirmar_edicion:
                if not codigo_nuevo or not descripcion_nueva or not categoria_nueva:
                    st.error("El código, la descripción y la categoría no pueden estar vacíos.")
                else:
                    try:
                        # No permitir cambiar unidad cuando existen movimientos:
                        # alteraría el significado de cantidades históricas.
                        if unidad_nueva != unidad_actual:
                            referencias = db().table("app_movimientos").select("id").eq(
                                "codigo", producto_seleccionado
                            ).limit(1).execute().data or []
                            if referencias:
                                raise ValueError(
                                    "No puedes cambiar la unidad de un producto con movimientos. "
                                    "Conserva la unidad original para no alterar el Kardex."
                                )
                        if codigo_nuevo != producto_seleccionado:
                            duplicados = db().table("app_productos").select("codigo").eq(
                                "codigo", codigo_nuevo
                            ).limit(1).execute().data or []
                            if duplicados:
                                raise ValueError("El nuevo código ya existe. Usa uno diferente.")
                        # Con las claves foráneas ON UPDATE CASCADE del SQL adjunto,
                        # la base actualiza los códigos relacionados en una operación atómica.
                        db().table("app_productos").update({
                            "codigo": codigo_nuevo,
                            "descripcion": descripcion_nueva,
                            "unidad": unidad_nueva,
                            "categoria": categoria_nueva
                        }).eq("codigo", producto_seleccionado).execute()
                        st.success("Producto actualizado correctamente")
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo editar: {e}")

        with pesta_eliminar:
            st.warning(
                "Solo se pueden eliminar productos sin movimientos ni conteos físicos. "
                "Los productos con historial deben conservarse para no afectar el Kardex."
            )
            st.write("**Producto:**", str(actual["descripcion"]))
            st.write("**Código:**", producto_seleccionado)
            with st.form("eliminar_producto_form"):
                confirmar_borrado = st.checkbox(
                    "Confirmo que deseo eliminar este producto del catálogo"
                )
                eliminar = st.form_submit_button("🗑️ Eliminar definitivamente")
            if eliminar:
                if not confirmar_borrado:
                    st.error("Marca la casilla de confirmación.")
                else:
                    try:
                        tiene_movimientos = db().table("app_movimientos").select("id").eq(
                            "codigo", producto_seleccionado
                        ).limit(1).execute().data or []
                        if tiene_movimientos:
                            st.error(
                                "No se puede eliminar: este producto tiene movimientos "
                                "de inventario. Se conserva para proteger el Kardex."
                            )
                        else:
                            # La FK de conteos físicos y otras relaciones de Supabase
                            # deben impedir el borrado si existen registros asociados.
                            tiene_conteos = db().table("app_conteos_fisicos").select("id").eq(
                                "codigo", producto_seleccionado
                            ).limit(1).execute().data or []
                            if tiene_conteos:
                                st.error(
                                    "No se puede eliminar: este producto tiene conteos físicos."
                                )
                            else:
                                db().table("app_productos").delete().eq(
                                    "codigo", producto_seleccionado
                                ).execute()
                                st.success("Producto eliminado del catálogo")
                                st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo eliminar: {e}")

with tabs[2]:
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

    panel_historial("INGRESO", "ingresos")


with tabs[3]:
    st.subheader(
        "Salida por guía PDF — revisión obligatoria"
    )

    st.warning(
        "La extracción del PDF puede equivocarse. "
        "Comprueba manualmente nombres y cantidades "
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

                if st.session_state.get("pdf_hash") != huella or st.session_state.get("pdf_catalogo") != hashlib.sha256(p.to_json().encode()).hexdigest():
                    st.session_state["pdf_hash"] = huella
                    st.session_state["pdf_catalogo"] = hashlib.sha256(p.to_json().encode()).hexdigest()
                    candidatos, no_reconocidas = detectar_candidatos_por_nombre(texto, p, archivo)
                    st.session_state["lineas_pdf"] = pd.DataFrame(candidatos, columns=["nombre", "cantidad"])
                    st.session_state["lineas_no_reconocidas"] = no_reconocidas

            except Exception as e:
                st.error(
                    f"No se pudo leer el PDF: {e}"
                )
                st.stop()

            st.caption("La lectura identifica NOMBRES y CANTIDADES en las columnas de la guía. Los códigos impresos solo ayudan a verificar. Revisa todo antes de descontar.")
            pendientes = st.session_state.get("lineas_no_reconocidas", [])
            if pendientes:
                with st.expander(f"Líneas no reconocidas automáticamente ({len(pendientes)})"):
                    st.text("\n".join(pendientes[:150]))
            st.caption("Puedes corregir, agregar o eliminar filas. Selecciona el nombre exacto del catálogo.")

            editadas = st.data_editor(
                st.session_state["lineas_pdf"],
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "nombre": st.column_config.SelectboxColumn(
                        "Nombre completo del producto",
                        options=p["descripcion"].dropna().tolist(),
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
                "He revisado el PDF, los nombres, "
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

                    if st.session_state.get("lineas_no_reconocidas"):
                        raise ValueError(
                            "Hay productos de la guía sin reconocer. "
                            "Regístralos en el catálogo y vuelve a cargar el PDF "
                            "antes de confirmar para evitar salidas incompletas."
                        )

                    if editadas.empty:
                        raise ValueError(
                            "Agrega al menos una línea"
                        )

                    if editadas[
                        ["nombre", "cantidad"]
                    ].isna().any().any():
                        raise ValueError(
                            "Hay nombres o cantidades vacíos"
                        )

                    lineas = []
                    # Solo nombres únicos: no se adivina un código si hay descripciones duplicadas.
                    conteos_nombres = p["descripcion"].value_counts()
                    mapa_nombres = dict(zip(p["descripcion"], p["codigo"]))
                    for fila in editadas.itertuples(index=False):
                        nombre = str(fila.nombre)
                        if nombre not in mapa_nombres or conteos_nombres[nombre] != 1:
                            raise ValueError(f"Nombre no encontrado o duplicado: {nombre}. Corrige el catálogo antes de continuar.")
                        if float(fila.cantidad) <= 0:
                            raise ValueError("La cantidad debe ser mayor que cero")
                        lineas.append({"codigo": str(mapa_nombres[nombre]), "cantidad": float(fila.cantidad)})

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
                    st.session_state.pop("pdf_catalogo", None)
                    st.session_state.pop("lineas_pdf", None)
                    st.session_state.pop("lineas_no_reconocidas", None)

                except Exception as e:
                    st.error(
                        f"No se registró la salida: {e}"
                    )

    panel_historial("SALIDA", "salidas")


with tabs[4]:
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

        nombres_k = dict(zip(k["codigo"].astype(str), k["descripcion"].fillna(k["codigo"]).astype(str)))
        elegido = st.selectbox(
            "Filtrar por producto", opciones,
            format_func=lambda c: "TODOS LOS PRODUCTOS" if c == "TODOS" else nombres_k.get(str(c), str(c))
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


with tabs[5]:
    st.subheader("Inventario físico — conteos guardados")
    st.caption("Los conteos se guardan en Supabase. No modifican el stock del Kardex.")
    s = stock()
    if s.empty:
        st.info("Todavía no hay productos")
    else:
        try:
            registros = pd.DataFrame(
                leer_todos("app_conteos_fisicos", orden="id", ascendente=False),
                columns=["id", "codigo", "cantidad", "stock_al_contar", "registrado_en", "observacion"]
            )
        except Exception as e:
            st.error("Primero ejecuta el SQL de conteo físico en Supabase.")
            st.code(str(e))
            st.stop()

        # El último conteo registrado de cada producto es el que se muestra.
        ultimos = (
            registros.drop_duplicates(subset=["codigo"], keep="first")
            if not registros.empty else pd.DataFrame(columns=["codigo", "cantidad", "registrado_en"])
        )
        fisico = s.merge(
            ultimos[["codigo", "cantidad", "registrado_en"]].rename(
                columns={"cantidad": "conteo_fisico", "registrado_en": "fecha_conteo"}
            ), on="codigo", how="left"
        )
        fisico["conteo_fisico"] = pd.to_numeric(fisico["conteo_fisico"], errors="coerce")
        fisico["diferencia"] = fisico["conteo_fisico"] - pd.to_numeric(fisico["stock"], errors="coerce")

        st.subheader("Registrar o corregir conteo")
        nombres = {str(r.codigo): str(r.descripcion) for r in s.itertuples()}
        with st.form("guardar_conteo_fisico", clear_on_submit=False):
            codigo_conteo = st.selectbox(
                "Producto a contar", list(nombres.keys()),
                format_func=lambda c: nombres.get(str(c), str(c))
            )
            anterior = ultimos[ultimos["codigo"] == codigo_conteo]
            if not anterior.empty:
                st.caption(f"Último conteo guardado: {float(anterior.iloc[0]['cantidad']):.3f}")
            else:
                st.caption("Este producto todavía no tiene conteo guardado")
            cantidad_conteo = st.number_input(
                "Cantidad física contada", min_value=0.0, value=0.0,
                step=1.0, format="%.3f"
            )
            observacion_conteo = st.text_input("Observación (opcional)")
            aceptar = st.checkbox("Confirmo que verifiqué esta cantidad en almacén")
            guardar_conteo = st.form_submit_button("Guardar conteo físico")

        if guardar_conteo:
            if not aceptar:
                st.warning("Marca la confirmación antes de guardar.")
            else:
                try:
                    stock_actual = stock()
                    valor_stock = float(stock_actual.loc[
                        stock_actual["codigo"] == codigo_conteo, "stock"
                    ].iloc[0])
                    db().table("app_conteos_fisicos").insert({
                        "codigo": codigo_conteo,
                        "cantidad": float(cantidad_conteo),
                        "stock_al_contar": valor_stock,
                        "observacion": observacion_conteo.strip()
                    }).execute()
                    st.success("Conteo guardado en Supabase. Se conservará al actualizar la página.")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo guardar el conteo: {e}")

        st.subheader("Último conteo por producto")
        st.caption("Sin conteo: casilla vacía. Una cantidad física de cero solo aparece si fue guardada.")
        st.dataframe(
            fisico[["codigo", "descripcion", "unidad", "stock", "conteo_fisico", "diferencia", "fecha_conteo"]],
            use_container_width=True, hide_index=True
        )
        st.download_button(
            "Descargar último inventario físico Excel", excel(fisico),
            "inventario_fisico_caferco.xlsx", key="descargar_ultimo_conteo"
        )
        st.subheader("Historial de conteos")
        if registros.empty:
            st.info("Aún no hay conteos guardados.")
        else:
            historico = registros.copy()
            historico["descripcion"] = historico["codigo"].map(nombres)
            historico["diferencia_al_contar"] = (
                pd.to_numeric(historico["cantidad"]) -
                pd.to_numeric(historico["stock_al_contar"])
            )
            st.dataframe(historico, use_container_width=True, hide_index=True)
            st.download_button(
                "Descargar historial de conteos Excel", excel(historico),
                "historial_conteos_caferco.xlsx", key="descargar_historial_conteo"
            )


with tabs[6]:
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
