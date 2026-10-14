from unittest.mock import patch

from odoo.tests import Form, TransactionCase, tagged
from odoo.tools import mute_logger

SALE_MODULE = 'odoo.addons.wb_so_cancel_guarderia.models.sale_order'


@tagged('post_install', '-at_install')
class TestCancelRelocation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.wh.delivery_steps = 'pick_pack_ship'
        cls.wh._wb_setup_cancel_flow()
        cls.guard = cls.wh.wb_cancel_location_id
        cls.can_type = cls.wh.wb_cancel_picking_type_id
        cls.pack_loc = cls.wh.wh_pack_stock_loc_id
        cls.out_loc = cls.wh.wh_output_stock_loc_id

        cls.categ = cls.env['product.category'].create({
            'name': 'AVCO test',
            'property_cost_method': 'average',
        })
        cls.product = cls._make_product('Producto CAN')
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente CAN'})
        cls._stock(cls.product, 100)
        # Stand-in del campo wmds_picked_qty de WMDs (el módulo no depende de WMDs).
        cls.env['ir.model.fields'].create({
            'name': 'x_wmds_picked_qty',
            'model_id': cls.env['ir.model']._get('stock.move.line').id,
            'field_description': 'Picked (test)',
            'ttype': 'float',
            'state': 'manual',
        })

    @classmethod
    def _make_product(cls, name):
        return cls.env['product.product'].create({
            'name': name,
            'is_storable': True,
            'type': 'consu',
            'categ_id': cls.categ.id,
            'standard_price': 10.0,
        })

    @classmethod
    def _stock(cls, product, qty):
        cls.env['stock.quant']._update_available_quantity(product, cls.wh.lot_stock_id, qty)

    def _make_so(self, qty=5, product=None):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.wh.id,
            'order_line': [(0, 0, {
                'product_id': (product or self.product).id,
                'product_uom_qty': qty,
            })],
        })
        so.action_confirm()
        return so

    def _step(self, so, ptype, qty=None):
        """Valida el picking pendiente del tipo dado (cantidades hechas = reservadas)."""
        picking = so.picking_ids.filtered(
            lambda p: p.picking_type_id == ptype and p.state not in ('done', 'cancel'))
        picking.action_assign()
        if qty is not None:
            picking.move_ids.quantity = qty
        picking.with_context(skip_backorder=True, skip_sms=True, skip_immediate=True).button_validate()
        return picking

    def _cancel(self, so):
        so.with_context(disable_cancel_warning=True).action_cancel()

    def _cans(self, so):
        return self.env['stock.picking'].search([
            ('picking_type_id', '=', self.can_type.id),
            ('origin', '=', 'Cancelacion %s' % so.name),
            ('state', '!=', 'cancel'),
        ])

    def _chatter(self, so):
        return ' '.join(so.message_ids.mapped('body'))

    def _svl_count(self):
        return self.env['stock.valuation.layer'].search_count([('product_id', '=', self.product.id)])

    # T1
    def test_t1_pick_pack_done_goes_to_guarderia(self):
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self._step(so, self.wh.pack_type_id)
        self._cancel(so)
        self.assertEqual(so.state, 'cancel')
        can = self._cans(so)
        self.assertEqual(len(can), 1)
        self.assertEqual(can.state, 'assigned')
        self.assertEqual(can.location_id, self.out_loc)
        self.assertEqual(can.location_dest_id, self.guard)
        self.assertEqual(can.move_ids.product_uom_qty, 5)
        self.assertFalse(can.group_id)
        svl_before = self._svl_count()
        can.with_context(skip_backorder=True, skip_sms=True, skip_immediate=True).button_validate()
        self.assertEqual(can.state, 'done')
        quant = self.env['stock.quant']._get_available_quantity(self.product, self.guard)
        self.assertEqual(quant, 5)
        self.assertEqual(self._svl_count(), svl_before, "El CAN no debe crear capas de valoración")
        self.assertIn(can.name, self._chatter(so))

    # T2
    def test_t2_pick_not_done_no_can(self):
        so = self._make_so(5)
        self._cancel(so)
        self.assertFalse(self._cans(so))
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.wh.lot_stock_id), 100)

    # T3
    def test_t3_pack_zone_included(self):
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self._cancel(so)
        can = self._cans(so)
        self.assertEqual(len(can), 1)
        self.assertEqual(can.location_id, self.pack_loc)
        self.assertEqual(can.location_dest_id, self.guard)
        self.assertEqual(can.move_ids.product_uom_qty, 5)

    # T4
    def test_t4_pack_zone_excluded(self):
        self.wh.wb_cancel_include_pack_zone = False
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self._cancel(so)
        self.assertFalse(self._cans(so))
        self.assertIn('sin reubicar', self._chatter(so))

    # T5
    def test_t5_two_orders_same_zone(self):
        so_a = self._make_so(5)
        so_b = self._make_so(3)
        for so in (so_a, so_b):
            self._step(so, self.wh.pick_type_id)
            self._step(so, self.wh.pack_type_id)
        self._cancel(so_a)
        can = self._cans(so_a)
        self.assertEqual(len(can), 1)
        self.assertEqual(can.move_ids.product_uom_qty, 5)
        self.assertEqual(can.state, 'assigned')
        out_b = so_b.picking_ids.filtered(lambda p: p.picking_type_id == self.wh.out_type_id)
        self.assertEqual(out_b.state, 'assigned')
        self.assertEqual(out_b.move_ids.quantity, 3)
        self.assertFalse(self._cans(so_b))

    # T6
    def test_t6_reopen_and_cancel_again_no_duplicates(self):
        """Odoo no regenera PICK/PACK al reabrir una SO cuyo flujo ya se hizo; lo que
        debe evitarse es que la 2.ª cancelación vuelva a trasladar lo ya trasladado."""
        for validate_first_can in (False, True):
            so = self._make_so(5)
            self._step(so, self.wh.pick_type_id)
            self._step(so, self.wh.pack_type_id)
            self._cancel(so)
            first = self._cans(so)
            self.assertEqual(first.move_ids.product_uom_qty, 5)
            if validate_first_can:
                first.with_context(skip_backorder=True, skip_sms=True, skip_immediate=True).button_validate()
            so.action_draft()
            so.action_confirm()
            self._cancel(so)
            self.assertEqual(self._cans(so), first, "No debe crearse un 2.º CAN duplicado")

    # T7
    def test_t7_sales_user_can_cancel(self):
        user = self.env['res.users'].create({
            'name': 'Vendedor CAN',
            'login': 'vendedor_can',
            'email': 'vendedor_can@example.com',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('sales_team.group_sale_salesman_all_leads').id,
            ])],
        })
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self._step(so, self.wh.pack_type_id)
        so.with_user(user).with_context(disable_cancel_warning=True).action_cancel()
        self.assertEqual(so.state, 'cancel')
        self.assertEqual(len(self._cans(so)), 1)

    # T8
    def test_t8_failure_does_not_block_cancel(self):
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        with patch(SALE_MODULE + '.SaleOrder._wb_relocate_cancelled_stock',
                   side_effect=Exception("boom")), mute_logger(SALE_MODULE):
            self._cancel(so)
        self.assertEqual(so.state, 'cancel')
        self.assertIn('no se pudo generar el traslado', self._chatter(so))
        self.assertFalse(self._cans(so))

    # T9
    def test_t9_setup_idempotent(self):
        Location = self.env['stock.location']
        PickingType = self.env['stock.picking.type']
        n_loc = Location.search_count([('name', '=', 'Guarderia_Cancelacion')])
        n_type = PickingType.search_count([('sequence_code', '=', 'CAN'), ('warehouse_id', '=', self.wh.id)])
        self.wh._wb_setup_cancel_flow()
        self.wh._wb_setup_cancel_flow()
        self.assertEqual(Location.search_count([('name', '=', 'Guarderia_Cancelacion')]), n_loc)
        self.assertEqual(PickingType.search_count(
            [('sequence_code', '=', 'CAN'), ('warehouse_id', '=', self.wh.id)]), n_type)
        self.assertEqual(self.guard.usage, 'internal')

    # T10
    def test_t10_partial_delivery_only_remainder(self):
        so = self._make_so(10)
        self._step(so, self.wh.pick_type_id)
        self._step(so, self.wh.pack_type_id)
        self._step(so, self.wh.out_type_id, qty=4)
        self._cancel(so)
        can = self._cans(so)
        self.assertEqual(len(can), 1)
        self.assertEqual(can.location_id, self.out_loc)
        self.assertEqual(can.move_ids.product_uom_qty, 6)

    # Extra: validación automática opcional
    def test_t14_auto_validate(self):
        self.wh.wb_cancel_auto_validate = True
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self._step(so, self.wh.pack_type_id)
        svl_before = self._svl_count()
        self._cancel(so)
        can = self._cans(so)
        self.assertEqual(can.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.guard), 5)
        self.assertEqual(self._svl_count(), svl_before)
        self.assertIn('validado automáticamente', self._chatter(so))

    # Extra: SO en borrador/enviada no hace nada
    def test_t11_draft_so_does_nothing(self):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.wh.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        self._cancel(so)
        self.assertEqual(so.state, 'cancel')
        self.assertFalse(self._cans(so))

    # Extra: almacén sin configuración -> advertencia, sin bloquear
    def test_t12_unconfigured_warehouse_warns(self):
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        self.wh.wb_cancel_location_id = False
        self._cancel(so)
        self.assertEqual(so.state, 'cancel')
        self.assertIn('no tiene configurados', self._chatter(so))

    # Extra: hallazgo sobre paquetes (documentado en el README)
    def test_t13_package_identity(self):
        so = self._make_so(5)
        self._step(so, self.wh.pick_type_id)
        pack = so.picking_ids.filtered(
            lambda p: p.picking_type_id == self.wh.pack_type_id and p.state not in ('done', 'cancel'))
        pack.action_assign()
        pack.action_put_in_pack()
        self.assertTrue(pack.move_line_ids.result_package_id)
        pack.with_context(skip_backorder=True, skip_sms=True, skip_immediate=True).button_validate()
        self._cancel(so)
        can = self._cans(so)
        self.assertEqual(can.state, 'assigned')
        package = can.move_line_ids.package_id
        # Hallazgo (ver README, "Paquetes"): el paquete se conserva en el CAN...
        self.assertTrue(package)
        can.with_context(skip_backorder=True, skip_sms=True, skip_immediate=True).button_validate()
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id), ('location_id', '=', self.guard.id)])
        # ...y llega a Guardería con su identidad intacta.
        self.assertEqual(quant.package_id, package)
        self.assertEqual(quant.quantity, 5)

    # Recolección en curso (WMDs): PICK sin validar con unidades ya recolectadas
    def _picked_so(self, qty, picked):
        self.enterContext(patch(SALE_MODULE + '.PICKED_FIELD', 'x_wmds_picked_qty'))
        so = self._make_so(qty)
        pick = so.picking_ids.filtered(lambda p: p.picking_type_id == self.wh.pick_type_id)
        self.assertEqual(pick.state, 'assigned')
        pick.move_line_ids.x_wmds_picked_qty = picked
        return so

    def test_t15_picked_in_progress_goes_to_guarderia(self):
        self.wh.wb_cancel_include_picked = True
        so = self._picked_so(5, 3)
        self._cancel(so)
        can = self._cans(so)
        self.assertEqual(len(can), 1)
        self.assertEqual(can.location_id, self.wh.lot_stock_id)
        self.assertEqual(can.location_dest_id, self.guard)
        self.assertEqual(can.move_ids.product_uom_qty, 3)
        self.assertEqual(can.move_ids.location_id, self.wh.lot_stock_id)
        self.assertEqual(can.state, 'assigned')
        self.assertIn('recolección en curso', self._chatter(so))

    def test_t16_picked_in_progress_disabled_by_default(self):
        so = self._picked_so(5, 3)
        self._cancel(so)
        self.assertFalse(self._cans(so))

    def test_t17_nothing_picked_no_can(self):
        self.wh.wb_cancel_include_picked = True
        so = self._picked_so(5, 0)
        self._cancel(so)
        self.assertFalse(self._cans(so))
