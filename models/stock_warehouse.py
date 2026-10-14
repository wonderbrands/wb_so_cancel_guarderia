from odoo import _, fields, models


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    wb_cancel_location_id = fields.Many2one(
        'stock.location', string='Ubicación Guardería', copy=False, check_company=True,
        domain="[('usage', '=', 'internal'), ('company_id', '=', company_id)]",
        help="Ubicación provisional donde se envía el stock de las SO canceladas.")
    wb_cancel_picking_type_id = fields.Many2one(
        'stock.picking.type', string='Tipo de operación de cancelación', copy=False, check_company=True,
        domain="[('code', '=', 'internal'), ('warehouse_id', '=', id)]",
        help="Tipo de operación (CAN) con el que se generan los traslados de cancelación.")
    wb_cancel_include_pack_zone = fields.Boolean(
        string='Incluir Packing Zone', default=True,
        help="Si está activo, también se reubica el stock que quedó en Packing Zone "
             "(PICK validado, PACK sin validar).")

    wb_cancel_include_picked = fields.Boolean(
        string='Incluir recolección en curso', default=False,
        help="Si está activo y WMDs está instalado, al cancelar una SO con PICK sin validar se "
             "trasladan a Guardería las unidades ya recolectadas (wmds_picked_qty) desde su "
             "ubicación de origen.")
    wb_cancel_auto_validate = fields.Boolean(
        string='Validar CAN automáticamente', default=False,
        help="Si está activo, los traslados de cancelación que queden totalmente reservados se "
             "validan al instante. Atención: el stock se mueve en el sistema sin que el operador "
             "lo lleve físicamente a Guardería.")

    def _wb_setup_cancel_flow(self):
        """Crea (solo si falta) la ubicación Guardería y el tipo de operación CAN.
        Idempotente: nunca sobrescribe lo ya configurado."""
        Location = self.env['stock.location'].sudo()
        PickingType = self.env['stock.picking.type'].sudo()
        for wh in self.sudo():
            if not wh.wb_cancel_location_id:
                # usage 'internal' a propósito: transit/inventory alterarían la valoración
                guard = Location.search([
                    ('name', '=', 'Guarderia_Cancelacion'),
                    ('location_id', '=', wh.view_location_id.id),
                    ('company_id', '=', wh.company_id.id),
                ], limit=1) or Location.create({
                    'name': 'Guarderia_Cancelacion',
                    'location_id': wh.view_location_id.id,
                    'usage': 'internal',
                    'barcode': '%sGUARDERIA' % wh.code,
                    'company_id': wh.company_id.id,
                })
                wh.wb_cancel_location_id = guard
            if not wh.wb_cancel_picking_type_id:
                ptype = PickingType.search([
                    ('warehouse_id', '=', wh.id),
                    ('sequence_code', '=', 'CAN'),
                    ('code', '=', 'internal'),
                ], limit=1)
                if not ptype:
                    vals = {
                        'name': 'Traslados de Cancelación',
                        'code': 'internal',
                        'sequence_code': 'CAN',
                        'warehouse_id': wh.id,
                        'company_id': wh.company_id.id,
                        'default_location_src_id': (wh.wh_output_stock_loc_id or wh.lot_stock_id).id,
                        'default_location_dest_id': wh.wb_cancel_location_id.id,
                        'reservation_method': 'at_confirm',
                        'create_backorder': 'always',
                        'barcode': '%sCAN' % wh.code,
                        'show_operations': True,
                        'use_create_lots': False,
                        'use_existing_lots': True,
                    }
                    # Opciones de stock_barcode (si está instalado): exigir escaneo
                    # de ubicación origen y destino.
                    fields_ = PickingType._fields
                    if 'restrict_scan_source_location' in fields_:
                        vals['restrict_scan_source_location'] = 'mandatory'
                    if 'restrict_scan_dest_location' in fields_:
                        vals['restrict_scan_dest_location'] = 'mandatory'
                    ptype = PickingType.create(vals)
                wh.wb_cancel_picking_type_id = ptype
        return True

    def action_wb_setup_cancel_flow(self):
        self._wb_setup_cancel_flow()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Cancelaciones de venta"),
                'message': _("Flujo de cancelación configurado."),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
