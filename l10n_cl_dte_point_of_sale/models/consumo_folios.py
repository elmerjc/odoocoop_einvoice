# -*- coding: utf-8 -*-
from odoo import fields, models, api, tools
from odoo.tools.translate import _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import dateutil.relativedelta as relativedelta
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
import pytz
import logging

_logger = logging.getLogger(__name__)

class ConsumoFolios(models.Model):
    _inherit = "account.move.consumo_folios"

    def _get_moves(self):
        recs = super(ConsumoFolios, self)._get_moves()
        tz = pytz.timezone('America/Santiago')
        next_day = (self.fecha_inicio + relativedelta.relativedelta(days=1)).strftime(DTF)
        orders_array = self.env['pos.order'].search(
            [
             ('invoice_id' , '=', False),
             ('sii_document_number', 'not in', [False, '0']),
             ('document_class_id.sii_code', 'in', [39, 41, 61]),
             ('date_order','>=', self.fecha_inicio.strftime(DTF)),
             ('date_order','<', next_day),
            ]
        ).with_context(lang='es_CL')
        for order in orders_array:
            recs.append(order)
        return recs

    def _get_totales(self, rec):
        if 'lines' not in rec:
            return super(ConsumoFolios, self)._get_totales(rec)
        Neto = 0
        MntExe = 0
        TaxMnt = 0
        MntTotal = 0
        # NC pasar a positivo
        TaxMnt =  rec.amount_tax if rec.amount_tax > 0 else rec.amount_tax * -1
        MntTotal = rec.amount_total if rec.amount_total > 0 else rec.amount_total * -1
        Neto = rec.pricelist_id.currency_id.round(sum(line.price_subtotal for line in rec.lines))
        if Neto < 0:
            Neto *= -1
        MntExe = rec.exento()
        TasaIVA = self.env['pos.order.line'].search([('order_id', '=', rec.id), ('tax_ids.amount', '>', 0)], limit=1).tax_ids.amount
        Neto -= MntExe
        return Neto, MntExe, TaxMnt, MntTotal, TasaIVA