# Cancelaciones de venta → Guardería (`wb_so_cancel_guarderia`)

Módulo de Odoo 18 Enterprise que, al **cancelar una orden de venta (SO)**, traslada automáticamente el stock
que ya había salido de su ubicación a **`WH/Guarderia_Cancelacion`**, usando un tipo de operación propio
**`WH/CAN` («Traslados de Cancelación»)**.

> **Estado:** Fase 1 completa, probada con tests automáticos (17) y en el ambiente del usuario (incluida la
> recolección en curso con WMDs real). Depende solo de `sale_stock`.

---

## 1. ¿Qué problema resuelve?

En un almacén con entrega en 3 pasos (**PICK → PACK → OUT**), si una venta se cancela cuando el producto ya
salió del rack, Odoo lo deja "varado" en `Packing Zone`, en `Output` o en el carrito. Nadie lo ve, el inventario
no cuadra y el producto se pierde de vista. Este módulo lo manda a un lugar conocido (**Guardería**) mediante un
traslado que el operador valida con Barcode, dejando trazabilidad.

## 2. ¿Qué hace, según el momento de la cancelación?

| Momento de la cancelación | Dónde está el producto | Resultado |
|---|---|---|
| PICK sin validar, **nada recolectado** | En el rack | Nada: Odoo libera las reservas (nativo). |
| PICK sin validar, **con unidades ya recolectadas en WMDs** *(opcional)* | En el carrito/patín (el sistema aún lo ve en el rack) | CAN **rack(s) de origen → Guardería** con lo recolectado. |
| PICK validado, PACK sin validar | `Packing Zone` | CAN `Packing Zone → Guardería` *(configurable)*. |
| PACK validado, OUT pendiente | `Output` | CAN `Output → Guardería`. |
| OUT validado parcialmente | `Output` (remanente) | CAN solo por el remanente. |
| OUT validado completo | Con el cliente | Nada: es una devolución normal. |

Reglas generales: solo productos **almacenables**, solo SO **confirmadas** (borrador/enviada no hacen nada) y solo
almacenes con **entrega en 3 pasos**.

## 3. Cómo funciona (en palabras simples)

1. Se cancela la SO (cualquier usuario con permiso de ventas).
2. Odoo cancela lo pendiente y libera las reservas (comportamiento nativo).
3. El módulo calcula **cuánto dejó esa SO** en `Packing Zone` y `Output`: suma lo que entró y resta lo que salió,
   considerando solo los movimientos de **esa** SO. Así, si dos SO comparten el mismo producto y zona, cada
   cancelación mueve únicamente lo suyo.
4. Resta lo que ya se hubiera trasladado en CAN anteriores de la misma SO (si se reabre y se cancela otra vez, **no se duplica**).
5. Crea **un CAN por ubicación de origen**, lo confirma y lo reserva en la misma transacción.
6. Deja en el chatter de la SO los CAN generados y cualquier stock que quedó sin reubicar.

Para la recolección en curso, el módulo lee `wmds_picked_qty` **antes** de cancelar (cancelar el PICK borra esas
líneas) y genera un solo CAN con una línea por rack real de origen.

## 4. Protecciones

- **Nunca bloquea la cancelación:** si algo falla, la SO se cancela igual; queda el error en el log y un aviso en el chatter.
- **Permisos:** las operaciones de stock se hacen con `sudo`; un usuario solo de Ventas puede cancelar.
- **Sin impacto contable:** el CAN es Internal→Internal; no crea capas de valoración (verificado con AVCO).
- **Trazabilidad:** origen `Cancelacion <SO>` + mensaje en el chatter. No se asigna el grupo de la SO al CAN, para no alterar los contadores de entregas de la SO.
- **Avisos:** almacén sin configurar o stock fuera de alcance → advertencia en el chatter.

## 5. Instalación y configuración

1. El módulo está en `odoo/addons-custom/`. Instalar `wb_so_cancel_guarderia`.
2. Al instalar, se configuran solos los almacenes con entrega en 3 pasos (ubicación Guardería + tipo CAN).
   Para otros almacenes: usar el botón **Configurar flujo de cancelación** (es idempotente).
3. Si ya estaba instalado: actualizar con `-u wb_so_cancel_guarderia` para ver los campos nuevos.

**Dónde se configura:** Inventario → Configuración → Almacenes → *(almacén)* → pestaña **Configuración del
almacén** → grupo **Cancelaciones de venta** (visible solo con entrega en 3 pasos).

| Campo | Qué hace | Por defecto |
|---|---|---|
| Ubicación Guardería | Destino de los traslados (interna, barcode `<WH>GUARDERIA`). | Se crea sola |
| Tipo de operación de cancelación | El tipo CAN (barcode `<WH>CAN`, reserva al confirmar, backorder siempre). | Se crea solo |
| Incluir Packing Zone | Reubica también lo que quedó en `Packing Zone`. | Sí |
| Incluir recolección en curso | Traslada lo ya recolectado en WMDs cuando el PICK no se validó. Requiere WMDs. | No |
| Validar CAN automáticamente | Valida el CAN al instante si queda totalmente reservado. | No |

> ⚠️ **Validar CAN automáticamente** mueve el stock en el sistema sin que el operador lo lleve físicamente a
> Guardería: el inventario puede quedar desalineado con la mercancía. Úsalo solo si otro proceso hace el traslado físico.

Si `stock_barcode` está instalado, el CAN exige escanear ubicación origen y destino.

## 6. Operación diaria

Ver el SOP para operadores: [`docs/SOP_operador_cancelaciones.md`](docs/SOP_operador_cancelaciones.md)
(Barcode → tarjeta *Traslados de Cancelación* → escanear origen, producto, Guardería → validar).

## 7. Limitaciones conocidas

- **Lotes/series:** no soportados todavía (productos sin tracking). Hay `# TODO lotes` en los puntos exactos del código.
- **Resets de WMDs:** `cancel_batch` y `remove_picking_from_batch` ponen `wmds_picked_qty` en 0 sin cancelar la SO;
  si después se cancela la SO, esas unidades ya no se detectan.
- **Reservas compartidas:** `Packing Zone`/`Output` no pertenecen a ninguna SO; si otra operación reservó esas unidades, el
  CAN puede quedar con reserva incompleta (el chatter lo advierte).
- **Reapertura de SO:** Odoo 18 no regenera PICK/PACK/OUT al reabrir una SO cuyo flujo ya se hizo; el módulo solo garantiza que no se duplique el CAN.
- **Paquetes:** con *Poner en paquete* en PACK, el CAN reserva el paquete completo y **llega intacto a Guardería** (no se desempaca).
- **Salida de Guardería:** aún no hay procedimiento (ver fases).

## 8. Diferencias respecto al diseño original (Odoo 18)

- `stock.location.return_location` **no existe** en Odoo 18: no se usa.
- Opciones de Barcode (`restrict_scan_source_location`, `restrict_scan_dest_location`): se activan solo si el campo existe.
- El aviso de fallo en el chatter también está protegido (un usuario sin email haría fallar `message_post` y bloquearía la cancelación).
- Escenario "reabrir y validar PICK/PACK otra vez" no es alcanzable en Odoo 18 (ver §7); el test verifica que no se duplique.

## 9. Pruebas

17 tests (`tests/test_cancel_relocation.py`): flujo completo, PICK sin validar, Packing Zone incluida/excluida, dos SO
en la misma zona, reapertura sin duplicados, usuario de Ventas, fallo forzado, configuración idempotente, entrega parcial,
SO en borrador, almacén sin configurar, paquetes, validación automática y recolección en curso. La recolección en curso se
prueba con un campo sustituto (`x_wmds_picked_qty`); en el ambiente real se validó con WMDs.

```bash
cd <carpeta-raiz-de-odoo>
PYTHONPATH=. .venv/bin/python3 setup/odoo -c odoo.conf -d wb_cancel_test \
  -i sale_stock,stock_barcode,wb_so_cancel_guarderia --without-demo=all \
  --test-tags /wb_so_cancel_guarderia --stop-after-init --http-port=8099 --log-level=test
```
Usar siempre una BD desechable (`wb_cancel_test`), nunca la de producción.

## 10. Estructura

```
wb_so_cancel_guarderia/
├── models/stock_warehouse.py   # campos de configuración + _wb_setup_cancel_flow()
├── models/sale_order.py        # _action_cancel y la lógica de reubicación
├── views/stock_warehouse_views.xml
├── hooks.py                    # configura almacenes de 3 pasos al instalar
├── tests/test_cancel_relocation.py
└── docs/SOP_operador_cancelaciones.md
```

## 11. Siguientes fases (propuesta)

| Fase | Tema | Notas |
|---|---|---|
| 2 | **Salida de Guardería** | Inspección → reingreso a `WH/Stock` o scrap, con su propio tipo de operación y SOP. |
| 3 | **Lotes/series** | Incluir `lot_id` en el saldo y crear los movimientos del CAN con el lote (`# TODO lotes`). |
| 4 | **Notificaciones** | Actividades/avisos a supervisores cuando hay CAN pendientes, reserva incompleta o stock sin reubicar. |
| 5 | **Reporte/tablero** | Stock actual en Guardería por SO de origen y antigüedad. |
| — | **Decisiones abiertas** | ¿Tipo de operación sin "escanear origen" para recolección en curso? ¿Comportamiento de paquetes en Guardería? ¿Auto-validación en algún escenario? |
