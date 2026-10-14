import logging
from collections import defaultdict

from odoo import _, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

# Campo de WMDs con las piezas ya recolectadas en una línea de PICK (módulo opcional).
PICKED_FIELD = 'wmds_picked_qty'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _action_cancel(self):
        # Solo SO confirmadas (con flujo de stock generado).
        to_relocate = self.filtered(lambda so: so.state == 'sale')
        # Las líneas del PICK (y su recolección) desaparecen al cancelar: hay que leerlas antes.
        in_progress = {}
        for so in to_relocate:
            try:
                in_progress[so.id] = so._wb_collect_picked_in_progress()
            except Exception:
                _logger.exception(
                    "wb_so_cancel_guarderia: fallo al leer recolección en curso de %s", so.name)
        # sale_stock cancela primero los pickings no 'done' (libera reservas) y
        # luego sale.order escribe state='cancel': al volver de super() el stock ya está libre.
        res = super()._action_cancel()
        for so in to_relocate:
            try:
                with self.env.cr.savepoint():
                    so._wb_relocate_cancelled_stock(in_progress.get(so.id))
            except Exception:
                _logger.exception(
                    "wb_so_cancel_guarderia: fallo al reubicar stock de %s", so.name)
                try:
                    with self.env.cr.savepoint():
                        so.message_post(body=_(
                            "ATENCIÓN: no se pudo generar el traslado de cancelación. "
                            "Revise el log del servidor y reubique el stock manualmente."))
                except Exception:
                    # Ni siquiera el aviso debe bloquear la cancelación.
                    _logger.exception(
                        "wb_so_cancel_guarderia: no se pudo dejar el aviso en %s", so.name)
        return res

    def _wb_collect_picked_in_progress(self):
        """Unidades ya recolectadas (según WMDs) en PICK sin validar.
        Devuelve {(location_id, product_id): qty en UdM del producto}."""
        self.ensure_one()
        so = self.sudo()
        wh = so.warehouse_id
        result = defaultdict(float)
        if not wh.wb_cancel_include_picked or PICKED_FIELD not in self.env['stock.move.line']._fields:
            return result
        picks = so.picking_ids.filtered(
            lambda p: p.picking_type_id == wh.pick_type_id and p.state not in ('done', 'cancel'))
        for line in picks.move_line_ids:
            picked = line[PICKED_FIELD]
            if picked <= 0 or line.location_id.usage != 'internal':
                continue
            # TODO lotes: incluir line.lot_id en la clave.
            result[(line.location_id.id, line.product_id.id)] += line.product_uom_id._compute_quantity(
                picked, line.product_id.uom_id, round=False)
        return result

    def _wb_auto_validate_picking(self, picking):
        """Valida el CAN; si falla, queda en 'Disponible' para validarlo a mano."""
        try:
            with self.env.cr.savepoint():
                picking.with_context(
                    skip_backorder=True, skip_sms=True, skip_immediate=True,
                ).button_validate()
        except Exception:
            _logger.exception(
                "wb_so_cancel_guarderia: no se pudo validar automáticamente %s", picking.name)

    def _wb_relocate_cancelled_stock(self, in_progress=None):
        self.ensure_one()
        so = self.sudo()
        wh = so.warehouse_id
        # Solo almacenes con entrega en 3 pasos: en otros no hay zonas intermedias.
        if not wh or wh.delivery_steps != 'pick_pack_ship':
            return
        can_type = wh.wb_cancel_picking_type_id
        guard = wh.wb_cancel_location_id
        if not (can_type and guard):
            self.message_post(body=_(
                "ATENCIÓN: el almacén %s no tiene configurados el tipo de operación "
                "de cancelación ni la ubicación Guardería. No se generó traslado.",
                wh.name))
            return

        pack_loc = wh.wh_pack_stock_loc_id
        out_loc = wh.wh_output_stock_loc_id
        watch = pack_loc | out_loc  # ubicaciones que se vigilan/reportan
        scope = (out_loc | pack_loc) if wh.wb_cancel_include_pack_zone else out_loc
        origin = "Cancelacion %s" % so.name  # no traducir: se usa para idempotencia

        # 1) Saldo neto por (ubicación, producto) dejado por el flujo de ESTA SO.
        # TODO lotes: cuando haya tracking, agregar lot_id a la clave del saldo.
        flow_types = wh.pick_type_id | wh.pack_type_id | wh.out_type_id
        done_lines = so.picking_ids.filtered(
            lambda p: p.picking_type_id in flow_types
        ).move_ids.filtered(lambda m: m.state == 'done').move_line_ids
        net = defaultdict(float)
        for line in done_lines:
            qty = line.product_uom_id._compute_quantity(
                line.quantity, line.product_id.uom_id, round=False)
            if line.location_dest_id in watch:
                net[(line.location_dest_id.id, line.product_id.id)] += qty
            if line.location_id in watch:
                net[(line.location_id.id, line.product_id.id)] -= qty

        # 2) Descontar CAN previos de esta SO (reapertura + nueva cancelación)
        prior_moves = self.env['stock.move'].sudo().search([
            ('picking_id.picking_type_id', '=', can_type.id),
            ('picking_id.origin', '=', origin),
            ('state', '!=', 'cancel'),
        ])
        for move in prior_moves:
            net[(move.location_id.id, move.product_id.id)] -= \
                move.product_uom._compute_quantity(
                    move.product_uom_qty, move.product_id.uom_id, round=False)

        # 3) Separar lo que se mueve (en alcance) de lo que solo se reporta
        to_move = defaultdict(list)  # location_id -> [(product, qty)]
        out_of_scope = []
        for (loc_id, prod_id), qty in net.items():
            product = self.env['product.product'].sudo().browse(prod_id)
            if not product.is_storable:
                continue
            if float_compare(qty, 0.0, precision_rounding=product.uom_id.rounding) <= 0:
                continue
            if loc_id in scope.ids:
                to_move[loc_id].append((product, qty))
            else:
                out_of_scope.append("%s x %s en %s" % (
                    product.display_name, qty,
                    self.env['stock.location'].sudo().browse(loc_id).complete_name))

        # 4) Un CAN por ubicación origen: crear → confirmar → reservar (misma transacción)
        created = self.env['stock.picking'].sudo()
        for loc_id, items in to_move.items():
            # No se asigna group_id de la SO: el CAN no debe contar como entrega de la SO.
            # TODO lotes: crear move_line_ids con lot_id en vez de depender de la reserva.
            picking = self.env['stock.picking'].sudo().create({
                'picking_type_id': can_type.id,
                'location_id': loc_id,
                'location_dest_id': guard.id,
                'origin': origin,
                'company_id': wh.company_id.id,
                'move_ids': [(0, 0, {
                    'name': origin,
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'product_uom_qty': qty,
                    'location_id': loc_id,
                    'location_dest_id': guard.id,
                    'company_id': wh.company_id.id,
                }) for product, qty in items],
            })
            picking.action_confirm()
            picking.action_assign()
            if wh.wb_cancel_auto_validate and picking.state == 'assigned':
                self._wb_auto_validate_picking(picking)
            created |= picking

        # 4b) Recolección en curso (PICK sin validar): un solo CAN con una línea por rack de origen.
        # Se genera solo si hay saldo; las unidades salieron físicamente del rack pero el
        # sistema aún las tiene allí.
        picked_items = []
        for (loc_id, prod_id), qty in (in_progress or {}).items():
            product = self.env['product.product'].sudo().browse(prod_id)
            if product.is_storable and float_compare(
                    qty, 0.0, precision_rounding=product.uom_id.rounding) > 0:
                picked_items.append((loc_id, product, qty))
        if picked_items:
            picking = self.env['stock.picking'].sudo().create({
                'picking_type_id': can_type.id,
                'location_id': wh.lot_stock_id.id,
                'location_dest_id': guard.id,
                'origin': origin,
                'company_id': wh.company_id.id,
                'move_ids': [(0, 0, {
                    'name': origin,
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'product_uom_qty': qty,
                    'location_id': loc_id,
                    'location_dest_id': guard.id,
                    'company_id': wh.company_id.id,
                }) for loc_id, product, qty in picked_items],
            })
            picking.action_confirm()
            picking.action_assign()
            if wh.wb_cancel_auto_validate and picking.state == 'assigned':
                self._wb_auto_validate_picking(picking)
            created |= picking

        # 5) Rastro en el chatter de la SO
        notes = []
        for picking in created:
            if picking.location_id == wh.lot_stock_id:
                src = "recolección en curso, desde: " + ", ".join(
                    sorted(set(picking.move_ids.location_id.mapped('complete_name'))))
            else:
                src = picking.location_id.complete_name
            txt = "%s (%s → %s)" % (picking.name, src, guard.complete_name)
            if picking.state == 'done':
                txt += " — validado automáticamente"
            elif picking.state != 'assigned':
                txt += " — RESERVA INCOMPLETA (estado: %s), revisar stock en origen" % picking.state
            notes.append(txt)
        if notes:
            self.message_post(body=_(
                "Traslado(s) de cancelación generado(s): %s", "; ".join(notes)))
        if out_of_scope:
            self.message_post(body=_(
                "ATENCIÓN: stock de esta SO sin reubicar (fuera de alcance de la automatización): %s",
                "; ".join(out_of_scope)))
