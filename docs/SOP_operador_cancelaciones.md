# SOP — Operador de almacén: Traslados de Cancelación (CAN)

Cuando se cancela una venta, el sistema genera automáticamente traslados para llevar su material a **Guardería**.

1. En la app **Barcode**, abrir la tarjeta **Traslados de Cancelación** (el contador muestra los CAN pendientes).
2. Abrir el CAN. El campo *Origen* dice `Cancelacion <SO>`; los productos y cantidades ya vienen reservados.
   Puede haber **dos CAN por SO**: uno desde `Packing Zone` y otro desde `Output`.
3. Localizar físicamente el material de esa SO:
   - Mesa de empaque (`Packing Zone`): puede estar a medio empacar; recogerlo tal cual, **sin terminar de empacar**.
   - Zona de salida (`Output`): paquete etiquetado con la SO.
   - **No retirar material de otras SO.**
4. Escanear ubicación origen → producto → cantidad.
5. Llevar el material a Guardería y escanear su código (`<WH>GUARDERIA`).
6. **Validar.** Si queda un backorder, **no descartarlo**: indica la cantidad que falta por mover.
7. No desempacar ni reetiquetar. La inspección y el destino final se definen en una etapa posterior.

## Caso: cancelación durante la recolección (si está habilitado)
El CAN puede traer varias líneas con distintos racks de origen. Las unidades ya recolectadas están en el
carrito/patín, **no** en el rack: llévalas a Guardería tal cual y valida; no las regreses al rack. Si en el rack
físico sobran unidades porque aún no se recolectaron, no se tocan.
