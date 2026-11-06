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
