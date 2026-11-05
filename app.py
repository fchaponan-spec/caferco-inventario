  st.error("Configura una contraseña privada y segura en Streamlit Secrets.")
  st.stop()
 if not st.session_state.get("autenticado", False):
  st.title("🔒 CAFERCO | Acceso privado")
  st.caption("Kardex e Inventario Interno")
  with st.form("iniciar_sesion", clear_on_submit=True):
   usuario = st.text_input("Usuario")
   clave = st.text_input("Contraseña", type="password")
   enviar = st.form_submit_button("Ingresar")
  if enviar:
   valido = hmac.compare_digest(usuario.encode(), usuario_configurado.encode()) and hmac.compare_digest(clave.encode(), clave_configurada.encode())
   if valido:
    st.session_state["autenticado"] = True
    st.rerun()
   else:
    st.error("Usuario o contraseña incorrectos.")
  st.stop()
 with st.sidebar:
  st.caption("Sesión iniciada")
  if st.button("Cerrar sesión"):
   st.session_state["autenticado"] = False
   st.rerun()
exigir_acceso()
client()
st.title('📦 CAFERCO | Kardex e Inventario Interno')
st.caption('Datos guardados en Supabase · Las salidas PDF requieren revisión antes de descontar stock')
tabs=st.tabs(['📦 Productos','➕ Ingresos','📄 Salidas PDF','📊 Stock / Kardex','🧮 Inventario físico','⬇️ Respaldo'])
with tabs[0]:
 st.subheader('Catálogo de productos')
 with st.form('producto'):
  a,b=st.columns(2);codigo=a.text_input('Código único');descripcion=b.text_input('Descripción / medida');unidad=a.selectbox('Unidad',['UND','ROLLO','KG','CAJA','BOLSA','METRO','OTRA']);categoria=b.text_input('Categoría (mallas, clavos, etc.)')
  if st.form_submit_button('Guardar producto'):
   if not codigo.strip() or not descripcion.strip():st.error('Código y descripción son obligatorios.')
   else:
    try:
     add_product(codigo.strip(),descripcion.strip(),unidad,categoria.strip())
     st.success('Producto guardado')
    except Exception as e:st.error(f'No se pudo guardar el producto (verifica si el código ya existe): {e}')
 st.dataframe(products(),use_container_width=True,hide_index=True)
with tabs[1]:
 st.subheader('Registrar ingreso')
 p=products()
 if p.empty:st.info('Primero registra productos.')
 else:
  with st.form('ingreso'):
   codigo=st.selectbox('Producto',p.codigo,format_func=lambda x:f'{x} — {p.set_index("codigo").loc[x,"descripcion"]}')
   cantidad=st.number_input('Cantidad',min_value=0.001,value=1.0,step=1.0);documento=st.text_input('Documento de ingreso');nota=st.text_input('Observación')
   if st.form_submit_button('Registrar ingreso'):
    movement(codigo,'INGRESO',cantidad,documento,nota);st.success('Ingreso registrado.')
with tabs[2]:
 st.subheader('Preparar salida desde guía o factura PDF')
 st.warning('La lectura automática es asistida: identifica códigos registrados en el PDF, pero NO supone cantidades. Comprueba las cantidades y unidades contra el documento antes de confirmar.')
 upload=st.file_uploader('Sube guía de remisión o factura PDF',type=['pdf'])
 p=products()
 if upload and not p.empty:
  raw=extract_pdf(upload)
  import hashlib
  fingerprint=hashlib.sha256(upload.getvalue()).hexdigest()
  exists=document_exists(fingerprint)
  if exists:st.error('Este PDF ya fue procesado. No se permitirá descontarlo nuevamente.')
  elif not raw.strip():st.error('PDF sin texto seleccionable. Necesita OCR; no se puede procesar automáticamente en esta versión.')
  else:
   st.text_area('Texto extraído (para verificar)',raw[:12000],height=160)
   matches=candidates(raw,p)
   st.caption('Coincidencias por código de producto. Añade o corrige filas si el PDF no contiene códigos.')
   if matches.empty:matches=pd.DataFrame([{'codigo':p.iloc[0].codigo,'cantidad':0.0}])
   else:matches=matches[['codigo','cantidad']]
   edited=st.data_editor(matches,num_rows='dynamic',use_container_width=True,hide_index=True,column_config={'codigo':st.column_config.SelectboxColumn('Código',options=p.codigo.tolist(),required=True),'cantidad':st.column_config.NumberColumn('Cantidad a descontar',min_value=0.0,step=1.0,required=True)},key=fingerprint)
   docnum=st.text_input('Número de guía / factura (obligatorio)')
   confirm=st.checkbox('He verificado códigos, cantidades y unidades contra el PDF')
   if st.button('Confirmar y descontar stock',type='primary',disabled=not confirm):
    try:
     rows=edited.to_dict('records');totals={}
     for r in rows:
      code=str(r['codigo']);q=float(r['cantidad'])
      if code not in set(p.codigo) or q<=0:raise ValueError('Todos los códigos deben existir y las cantidades deben ser mayores a cero.')
      totals[code]=totals.get(code,0)+q
     if not docnum.strip():raise ValueError('Indica el número del documento.')
     available=stock().set_index('codigo')['stock'].to_dict()
     for code,q in totals.items():
      if q>available.get(code,0):raise ValueError(f'Stock insuficiente de {code}: disponible {available.get(code,0)}, salida {q}')
     register_pdf(fingerprint,docnum.strip(),upload.name,totals)
     st.success('Salida descontada correctamente.');st.rerun()
    except Exception as e:st.error(f'No se pudo registrar la salida: {e}')
with tabs[3]:
 st.subheader('Stock actualizado')
 s=stock();st.dataframe(s,use_container_width=True,hide_index=True)
 st.download_button('Descargar stock Excel',excel(s),'stock_caferco.xlsx')
 st.subheader('Historial Kardex')
 m=kardex()
 st.dataframe(m,use_container_width=True,hide_index=True)
 st.download_button('Descargar Kardex Excel',excel(m),'kardex_caferco.xlsx')
with tabs[4]:
 st.subheader('Conteo físico y diferencias')
 s=stock()
 if not s.empty:
  physical=s[['codigo','descripcion','unidad','stock']].copy();physical['conteo_fisico']=0.0
  edited=st.data_editor(physical,use_container_width=True,hide_index=True,disabled=['codigo','descripcion','unidad','stock'],column_config={'conteo_fisico':st.column_config.NumberColumn('Conteo físico',min_value=0.0,step=1.0)},key='conteo')
  edited['diferencia']=edited['conteo_fisico']-edited['stock']
  st.dataframe(edited,use_container_width=True,hide_index=True)
  st.download_button('Descargar comparación Excel',excel(edited),'inventario_fisico.xlsx')
  st.info('Los conteos no modifican el stock automáticamente. Para iniciar diciembre con stock físico real, registra ajustes autorizados después de revisar diferencias.')
  with st.expander('Aplicar ajuste autorizado a un producto'):
   code=st.selectbox('Código a ajustar',s.codigo);count=st.number_input('Cantidad física confirmada',min_value=0.0,step=1.0);reason=st.text_input('Motivo / acta de inventario')
   if st.button('Aplicar ajuste'):
    if not reason.strip():st.error('Indica motivo o acta.')
    else:
     current=float(s.set_index('codigo').loc[code,'stock']);delta=count-current
     if abs(delta)>0.000001:movement(code,'AJUSTE+' if delta>0 else 'AJUSTE-',abs(delta),'INVENTARIO',reason)
     st.success('Ajuste aplicado.');st.rerun()
with tabs[5]:
 st.subheader('Copia de seguridad')
 st.info('Los datos se guardan en Supabase. Descarga periódicamente los siguientes archivos Excel como respaldo.')
 st.download_button('Descargar productos Excel',excel(products()),'productos_caferco.xlsx')
 st.download_button('Descargar movimientos Excel',excel(movements()),'movimientos_caferco.xlsx')
 st.download_button('Descargar documentos Excel',excel(documents()),'documentos_caferco.xlsx')
