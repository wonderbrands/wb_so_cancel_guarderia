def post_init_hook(env):
    warehouses = env['stock.warehouse'].search([('delivery_steps', '=', 'pick_pack_ship')])
    warehouses._wb_setup_cancel_flow()
