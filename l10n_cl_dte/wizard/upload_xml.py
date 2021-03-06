# -*- coding: utf-8 -*-
from openerp import models, fields, api, _
from openerp.exceptions import UserError
import logging
import base64
import xmltodict
from lxml import etree
import collections
import dicttoxml

_logger = logging.getLogger(__name__)

try:
    from signxml import xmldsig, methods
except ImportError:
    _logger.info('Cannot import signxml')

BC = '''-----BEGIN CERTIFICATE-----\n'''
EC = '''\n-----END CERTIFICATE-----\n'''

class UploadXMLWizard(models.TransientModel):
    _name = 'sii.dte.upload_xml.wizard'
    _description = 'SII XML from Provider'

    action = fields.Selection([
            ('create_po','Crear Orden de Pedido y Factura'),
            ('create','Crear Solamente Factura'),
            ], string="Acción", default="create")

    xml_file = fields.Binary(
        string='XML File', filters='*.xml',
        store=True, help='Upload the XML File in this holder')
    filename = fields.Char('File Name')
    inv = fields.Many2one('account.invoice', invisible=True)

    @api.multi
    def confirm(self):
        context = dict(self._context or {})
        active_id = context.get('active_id', []) or []
        created_inv = []
        if self.action == 'create':
            self.do_create_inv()
            if self.inv:
                created_inv.append(self.inv.id)
            xml_id = 'account.action_invoice_tree2'
        if self.action == 'create_po':
            self.do_create_po()
            xml_id = 'purchase.purchase_order_tree'
        result = self.env.ref('%s' % (xml_id)).read()[0]
        invoice_domain = eval(result['domain'])
        invoice_domain.append(('id', 'in', created_inv))
        result['domain'] = invoice_domain
        return result

    def format_rut(self, RUTEmisor=None):
        rut = RUTEmisor.replace('-','')
        if int(rut[:-1]) < 10000000:
            rut = '0' + str(int(rut))
        rut = 'CL'+rut
        return rut

    def _read_xml(self, mode="text"):
        if self.xml_file:
            xml = base64.b64decode(self.xml_file).decode('ISO-8859-1').replace('<?xml version="1.0" encoding="ISO-8859-1"?>','')
        else:
            xml = self.inv.sii_xml_request.decode('ISO-8859-1').replace('<?xml version="1.0" encoding="ISO-8859-1"?>','')
        if mode == "etree":
            return etree.fromstring(xml)
        if mode == "parse":
            return xmltodict.parse(xml)
        return xml

    def _check_digest_caratula(self):
        xml = etree.fromstring(self._read_xml(False))
        string = etree.tostring(xml[0])
        mess = etree.tostring(etree.fromstring(string), method="c14n")
        our = base64.b64encode(self.inv.digest(mess))
        if our != xml.find("{http://www.w3.org/2000/09/xmldsig#}Signature/{http://www.w3.org/2000/09/xmldsig#}SignedInfo/{http://www.w3.org/2000/09/xmldsig#}Reference/{http://www.w3.org/2000/09/xmldsig#}DigestValue").text:
            return 2, 'Envio Rechazado - Error de Firma'
        return 0, 'Envio Ok'

    def _check_digest_dte(self, dte):
        xml = self._read_xml("etree")
        envio = xml.find("{http://www.sii.cl/SiiDte}SetDTE")#"{http://www.w3.org/2000/09/xmldsig#}Signature/{http://www.w3.org/2000/09/xmldsig#}SignedInfo/{http://www.w3.org/2000/09/xmldsig#}Reference/{http://www.w3.org/2000/09/xmldsig#}DigestValue").text
        for e in envio.findall("{http://www.sii.cl/SiiDte}DTE") :
            string = etree.tostring(e.find("{http://www.sii.cl/SiiDte}Documento"))#doc
            mess = etree.tostring(etree.fromstring(string), method="c14n").replace(' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"','')# el replace es necesario debido a que python lo agrega solo
            our = base64.b64encode(self.inv.digest(mess))
            if our != e.find("{http://www.w3.org/2000/09/xmldsig#}Signature/{http://www.w3.org/2000/09/xmldsig#}SignedInfo/{http://www.w3.org/2000/09/xmldsig#}Reference/{http://www.w3.org/2000/09/xmldsig#}DigestValue").text:
                return 1, 'DTE No Recibido - Error de Firma'
        return 0, 'DTE Recibido OK'

    def _validar_caratula(self, cara):
        if not self.env['res.company'].search([
                ('vat','=', self.format_rut(cara['RutReceptor']))
            ]):
            return 3, 'Rut no corresponde a nuestra empresa'
        partner_id = self.env['res.partner'].search([
        ('active','=', True),
        ('parent_id', '=', False),
        ('vat','=', self.format_rut(cara['RutEmisor']))])
        if not partner_id and not self.inv:
            return 2, 'Rut no coincide con los registros'
        try:
            self.inv.xml_validator(self._read_xml(False), 'env')
        except:
               return 1, 'Envio Rechazado - Error de Schema'
        #for SubTotDTE in cara['SubTotDTE']:
        #    sii_document_class = self.env['sii.document_class'].search([('sii_code','=', str(SubTotDTE['TipoDTE']))])
        #    if not sii_document_class:
        #        return  99, 'Tipo de documento desconocido'
        return 0, 'Envío Ok'

    def _validar(self, doc):
        cara, glosa = self._validar_caratula(doc[0][0]['Caratula'])

        return cara, glosa

    def _validar_dte(self, doc):
        res = collections.OrderedDict()
        res['TipoDTE'] = doc['Encabezado']['IdDoc']['TipoDTE']
        res['Folio'] = doc['Encabezado']['IdDoc']['Folio']
        res['FchEmis'] = doc['Encabezado']['IdDoc']['FchEmis']
        res['RUTEmisor'] = doc['Encabezado']['Emisor']['RUTEmisor']
        res['RUTRecep'] = doc['Encabezado']['Receptor']['RUTRecep']
        res['MntTotal'] = doc['Encabezado']['Totales']['MntTotal']
        partner_id = self.env['res.partner'].search([
        ('active','=', True),
        ('parent_id', '=', False),
        ('vat','=', self.format_rut(doc['Encabezado']['Emisor']['RUTEmisor']))])
        sii_document_class = self.env['sii.document_class'].search([('sii_code','=', str(doc['Encabezado']['IdDoc']['TipoDTE']))])
        res['EstadoRecepDTE'] = 0
        res['RecepDTEGlosa'] = 'DTE Recibido OK'
        res['EstadoRecepDTE'], res['RecepDTEGlosa'] = self._check_digest_dte(doc)
        if not sii_document_class:
            res['EstadoRecepDTE'] = 99
            res['RecepDTEGlosa'] = 'Tipo de documento desconocido'
            return res
        docu = self.env['account.invoice'].search(
            [
                ('reference','=', doc['Encabezado']['IdDoc']['Folio']),
                ('partner_id','=',partner_id.id),
                ('sii_document_class_id','=',sii_document_class.id)
            ])
        company_id = self.env['res.company'].search([
                ('vat','=', self.format_rut(doc['Encabezado']['Receptor']['RUTRecep']))
            ])
        if not company_id and (not docu or doc['Encabezado']['Receptor']['RUTRecep'] != self.env['account.invoice'].format_vat(docu.company_id.vat) ) :
            res['EstadoRecepDTE'] = 3
            res['RecepDTEGlosa'] = 'Rut no corresponde a la empresa esperada'
            return res
        return res

    def _validar_dtes(self):
        envio = self._read_xml('parse')
        if 'Documento' in envio['EnvioDTE']['SetDTE']['DTE']:
            res = {'RecepcionDTE' : self._validar_dte(envio['EnvioDTE']['SetDTE']['DTE']['Documento'])}
        else:
            res = []
            for doc in envio['EnvioDTE']['SetDTE']['DTE']:
                res.extend([ {'RecepcionDTE' : self._validar_dte(doc['Documento'])} ])
        return res

    def _caratula_respuesta(self, RutResponde, RutRecibe, IdRespuesta="1", NroDetalles=0):
        caratula = collections.OrderedDict()
        caratula['RutResponde'] = RutResponde
        caratula['RutRecibe'] =  RutRecibe
        caratula['IdRespuesta'] = IdRespuesta
        caratula['NroDetalles'] = NroDetalles
        caratula['NmbContacto'] = self.env.user.partner_id.name
        caratula['FonoContacto'] = self.env.user.partner_id.phone
        caratula['MailContacto'] = self.env.user.partner_id.email
        caratula['TmstFirmaResp'] = self.inv.time_stamp()
        return caratula

    def _receipt(self, IdRespuesta):
        envio = self._read_xml('parse')
        xml = self._read_xml('etree')
        resp = collections.OrderedDict()
        resp['NmbEnvio'] = self.filename or self.inv.sii_send_file_name
        resp['FchRecep'] = self.inv.time_stamp()
        resp['CodEnvio'] = self.inv._acortar_str(IdRespuesta, 10)
        resp['EnvioDTEID'] = xml[0].attrib['ID']
        resp['Digest'] = xml.find("{http://www.w3.org/2000/09/xmldsig#}Signature/{http://www.w3.org/2000/09/xmldsig#}SignedInfo/{http://www.w3.org/2000/09/xmldsig#}Reference/{http://www.w3.org/2000/09/xmldsig#}DigestValue").text
        EstadoRecepEnv, RecepEnvGlosa = self._validar_caratula(envio['EnvioDTE']['SetDTE']['Caratula'])
        if EstadoRecepEnv == 0:
            EstadoRecepEnv, RecepEnvGlosa = self._check_digest_caratula()
        resp['RutEmisor'] = envio['EnvioDTE']['SetDTE']['Caratula']['RutEmisor']
        resp['RutReceptor'] = envio['EnvioDTE']['SetDTE']['Caratula']['RutReceptor']
        resp['EstadoRecepEnv'] = EstadoRecepEnv
        resp['RecepEnvGlosa'] = RecepEnvGlosa
        NroDte = len(envio['EnvioDTE']['SetDTE']['DTE'])
        if 'Documento' in envio['EnvioDTE']['SetDTE']['DTE']:
            NroDte = 1
        resp['NroDTE'] = NroDte
        resp['item'] = self._validar_dtes()
        return resp

    def _RecepcionEnvio(self, Caratula, resultado):
        resp='''<?xml version="1.0" encoding="ISO-8859-1"?>
<RespuestaDTE version="1.0" xmlns="http://www.sii.cl/SiiDte" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sii.cl/SiiDte RespuestaEnvioDTE_v10.xsd" >
    <Resultado ID="Odoo_resp">
        <Caratula version="1.0">
            {0}
        </Caratula>
            {1}
    </Resultado>
</RespuestaDTE>'''.format(Caratula,resultado)
        return resp

    def _create_attachment(self, xml, name):
        data = base64.b64encode(xml)
        filename = (name + '.xml').replace(' ','')
        url_path = '/web/binary/download_document?model=account.invoice\
    &field=sii_xml_request&id=%s&filename=%s' % (self.inv.id, filename)
        att = self.env['ir.attachment'].search([
                                                ('name','=', filename),
                                                ('res_id','=', self.inv.id),
                                                ('res_model','=','account.invoice')],
                                                limit=1)
        if att:
            return att
        values = dict(
                        name=filename,
                        datas_fname=filename,
                        url=url_path,
                        res_model='account.invoice',
                        res_id=self.inv.id,
                        type='binary',
                        datas=data,
                    )
        att = self.env['ir.attachment'].create(values)
        return att

    def do_receipt_deliver(self):
        envio = self._read_xml('parse')
        company_id = self.env['res.company'].search(
            [
                ('vat','=', self.format_rut(envio['EnvioDTE']['SetDTE']['Caratula']['RutReceptor']))
            ],
            limit=1)
        id_seq = self.env.ref('l10n_cl_dte.response_sequence').id
        IdRespuesta = self.env['ir.sequence'].browse(id_seq).next_by_id()
        try:
            signature_d = self.env['account.invoice'].get_digital_signature(company_id)
        except:
            raise UserError(_('''There is no Signer Person with an \
        authorized signature for you in the system. Please make sure that \
        'user_signature_key' module has been installed and enable a digital \
        signature, for you or make the signer to authorize you to use his \
        signature.'''))
        certp = signature_d['cert'].replace(
            BC, '').replace(EC, '').replace('\n', '')
        recep = self._receipt(IdRespuesta)
        NroDetalles = len(envio['EnvioDTE']['SetDTE']['DTE'])
        dicttoxml.set_debug(False)
        resp_dtes = dicttoxml.dicttoxml(recep, root=False, attr_type=False).replace('<item>','\n').replace('</item>','\n')
        RecepcionEnvio = '''<RecepcionEnvio>
                    {0}
                    </RecepcionEnvio>
                    '''.format(resp_dtes)
        RutRecibe = envio['EnvioDTE']['SetDTE']['Caratula']['RutEmisor']
        caratula_recepcion_envio = self._caratula_respuesta(
            self.env['account.invoice'].format_vat(company_id.vat),
            RutRecibe,
            IdRespuesta,
            NroDetalles)
        caratula = dicttoxml.dicttoxml(caratula_recepcion_envio,
                                       root=False,
                                       attr_type=False).replace('<item>','\n').replace('</item>','\n')
        resp = self._RecepcionEnvio(caratula, RecepcionEnvio )
        respuesta = self.inv.sign_full_xml(
            resp,
            signature_d['priv_key'],
            certp,
            'Odoo_resp',
            'env_resp')
        if self.inv:
            self.inv.sii_xml_response = respuesta
        att = self._create_attachment(respuesta, 'recepcion_envio_' + (self.filename or self.inv.sii_send_file_name) + '_' + str(IdRespuesta))
        if self.inv.partner_id and  att:
            self.inv.message_post(
                body='XML de Respuesta Envío, Estado: %s , Glosa: %s ' % (recep['EstadoRecepEnv'], recep['RecepEnvGlosa'] ),
                subject='XML de Respuesta Envío' ,
                partner_ids=[self.inv.partner_id.id],
                attachment_ids=[ att.id ],
                message_type='comment', subtype='mt_comment')

    def _validar_dte_en_envio(self, doc, IdRespuesta):
        res = collections.OrderedDict()
        res['TipoDTE'] = doc['Encabezado']['IdDoc']['TipoDTE']
        res['Folio'] = doc['Encabezado']['IdDoc']['Folio']
        res['FchEmis'] = doc['Encabezado']['IdDoc']['FchEmis']
        res['RUTEmisor'] = doc['Encabezado']['Emisor']['RUTEmisor']
        res['RUTRecep'] = doc['Encabezado']['Receptor']['RUTRecep']
        res['MntTotal'] = doc['Encabezado']['Totales']['MntTotal']
        res['CodEnvio'] = str(IdRespuesta) + str(doc['Encabezado']['IdDoc']['Folio'])
        partner_id = self.env['res.partner'].search([
        ('active','=', True),
        ('parent_id', '=', False),
        ('vat','=', self.format_rut(doc['Encabezado']['Emisor']['RUTEmisor']))])
        sii_document_class = self.env['sii.document_class'].search([('sii_code','=', str(doc['Encabezado']['IdDoc']['TipoDTE']))])
        res['EstadoDTE'] = 0
        res['EstadoDTEGlosa'] = 'DTE Aceptado OK'
        if not sii_document_class:
            res['EstadoDTE'] = 2
            res['EstadoDTEGlosa'] = 'DTE Rechazado'
            res['CodRchDsc'] = "-1"
            return res

        if doc['Encabezado']['Receptor']['RUTRecep'] != self.inv.company_id.partner_id.document_number:
            res['EstadoDTE'] = 2
            res['EstadoDTEGlosa'] = 'DTE Rechazado'
            res['CodRchDsc'] = "-1"
            return res

        if int(round(self.inv.amount_total)) != int(round(doc['Encabezado']['Totales']['MntTotal'])):
            res['EstadoDTE'] = 2
            res['EstadoDTEGlosa'] = 'DTE Rechazado'
            res['CodRchDsc'] = "-1"
        #@TODO hacer más Validaciones, como por ejemplo, valores por línea
        return res

    def _resultado(self, IdRespuesta):
        envio = self._read_xml('parse')
        if 'Documento' in envio['EnvioDTE']['SetDTE']['DTE']:
            return {'ResultadoDTE' : self._validar_dte_en_envio(envio['EnvioDTE']['SetDTE']['DTE']['Documento'],IdRespuesta)}
        else:
            for doc in envio['EnvioDTE']['SetDTE']['DTE']:
                if doc['Documento']['Encabezado']['IdDoc']['Folio'] == self.inv.reference:
                    return {'ResultadoDTE' : self._validar_dte_en_envio(doc['Documento'], IdRespuesta)}
        return False

    def _ResultadoDTE(self, Caratula, resultado):
        resp='''<?xml version="1.0" encoding="ISO-8859-1"?>
<RespuestaDTE version="1.0" xmlns="http://www.sii.cl/SiiDte" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sii.cl/SiiDte RespuestaEnvioDTE_v10.xsd" >
    <Resultado ID="Odoo_resp">
        <Caratula version="1.0">
            {0}
        </Caratula>
            {1}
    </Resultado>
</RespuestaDTE>'''.format(Caratula,resultado)

        return resp

    def do_validar_comercial(self):
        id_seq = self.env.ref('l10n_cl_dte.response_sequence').id
        IdRespuesta = self.env['ir.sequence'].browse(id_seq).next_by_id()
        for inv in self.inv:
            if inv.estado_recep_dte not in ['0']:
                try:
                    signature_d = inv.get_digital_signature(inv.company_id)
                except:
                    raise UserError(_('''There is no Signer Person with an \
                authorized signature for you in the system. Please make sure that \
                'user_signature_key' module has been installed and enable a digital \
                signature, for you or make the signer to authorize you to use his \
                signature.'''))
                certp = signature_d['cert'].replace(
                    BC, '').replace(EC, '').replace('\n', '')
                dte = self._resultado(IdRespuesta)
        envio = self._read_xml('parse')
        NroDetalles = len(envio['EnvioDTE']['SetDTE']['DTE'])
        if 'Documento' in envio['EnvioDTE']['SetDTE']['DTE']:
            NroDetalles = 1
        dicttoxml.set_debug(False)
        ResultadoDTE = dicttoxml.dicttoxml(dte, root=False, attr_type=False).replace('<item>','\n').replace('</item>','\n')
        RutRecibe = envio['EnvioDTE']['SetDTE']['Caratula']['RutEmisor']
        caratula_validacion_comercial = self._caratula_respuesta(
            self.env['account.invoice'].format_vat(inv.company_id.vat),
            RutRecibe,
            IdRespuesta,
            NroDetalles)
        caratula = dicttoxml.dicttoxml(caratula_validacion_comercial,
                                       root=False,
                                       attr_type=False).replace('<item>','\n').replace('</item>','\n')
        resp = self._ResultadoDTE(caratula, ResultadoDTE)
        respuesta = self.inv.sign_full_xml(
            resp,
            signature_d['priv_key'],
            certp,
            'Odoo_resp',
            'env_resp')
        if self.inv:
            self.inv.sii_message = respuesta
        att = self._create_attachment(respuesta, 'validacion_comercial_' + str(IdRespuesta))
        self.inv.message_post(
            body='XML de Validación Comercial, Estado: %s, Glosa: %s' % (dte['ResultadoDTE']['EstadoDTE'], dte['ResultadoDTE']['EstadoDTEGlosa']),
            subject='XML de Validación Comercial',
            partner_ids=[self.inv.partner_id.id],
            attachment_ids=[ att.id ],
            message_type='comment', subtype='mt_comment')

    def _recep(self, inv, RutFirma):
        receipt = collections.OrderedDict()
        receipt['TipoDoc'] = inv.sii_document_class_id.sii_code
        receipt['Folio'] = int(inv.reference)
        receipt['FchEmis'] = inv.date_invoice
        receipt['RUTEmisor'] = inv.format_vat(inv.partner_id.vat)
        receipt['RUTRecep'] = inv.format_vat(inv.company_id.vat)
        receipt['MntTotal'] = int(round(inv.amount_total))
        receipt['Recinto'] = inv.company_id.street
        receipt['RutFirma'] = RutFirma
        receipt['Declaracion'] = 'El acuse de recibo que se declara en este acto, de acuerdo a lo dispuesto en la letra b) del Art. 4, y la letra c) del Art. 5 de la Ley 19.983, acredita que la entrega de mercaderias o servicio(s) prestado(s) ha(n) sido recibido(s).'
        receipt['TmstFirmaRecibo'] = inv.time_stamp()
        return receipt

    def _envio_recep(self,caratula, recep):
        xml = '''<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioRecibos xmlns='http://www.sii.cl/SiiDte' xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' xsi:schemaLocation='http://www.sii.cl/SiiDte EnvioRecibos_v10.xsd' version="1.0">
    <SetRecibos ID="SetDteRecibidos">
        <Caratula version="1.0">
        {0}
        </Caratula>
        {1}
    </SetRecibos>
</EnvioRecibos>'''.format(caratula, recep)
        return xml

    def _caratula_recep(self, RutResponde, RutRecibe):
        caratula = collections.OrderedDict()
        caratula['RutResponde'] = RutResponde
        caratula['RutRecibe'] = RutRecibe
        caratula['NmbContacto'] = self.env.user.partner_id.name
        caratula['FonoContacto'] = self.env.user.partner_id.phone
        caratula['MailContacto'] = self.env.user.partner_id.email
        caratula['TmstFirmaEnv'] = self.inv.time_stamp()
        return caratula

    @api.multi
    def do_receipt(self):
        receipts = ""
        message = ""
        for inv in self.inv:
            if inv.estado_recep_dte not in ['0']:
                try:
                    signature_d = inv.get_digital_signature(inv.company_id)
                except:
                    raise UserError(_('''There is no Signer Person with an \
                authorized signature for you in the system. Please make sure that \
                'user_signature_key' module has been installed and enable a digital \
                signature, for you or make the signer to authorize you to use his \
                signature.'''))
                certp = signature_d['cert'].replace(
                    BC, '').replace(EC, '').replace('\n', '')
                dict_recept = self._recep( inv, signature_d['subject_serial_number'] )
                id = "T" + str(inv.sii_document_class_id.sii_code) + "F" + str(inv.get_folio())
                doc = '''
        <Recibo version="1.0" xmlns="http://www.sii.cl/SiiDte" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sii.cl/SiiDte Recibos_v10.xsd" >
            <DocumentoRecibo ID="{0}" >
            {1}
            </DocumentoRecibo>
        </Recibo>
                '''.format(id, dicttoxml.dicttoxml(dict_recept, root=False, attr_type=False))
                message += '\n ' + str(dict_recept['Folio']) + ' ' + dict_recept['Declaracion']
                receipt = self.inv.sign_full_xml(
                    doc,
                    signature_d['priv_key'],
                    certp,
                    'Recibo',
                    'recep')
                receipts += "\n" + receipt
        envio = self._read_xml('parse')
        RutRecibe = envio['EnvioDTE']['SetDTE']['Caratula']['RutEmisor']
        dict_caratula = self._caratula_recep(self.env['account.invoice'].format_vat(inv.company_id.vat), RutRecibe)
        caratula = dicttoxml.dicttoxml(dict_caratula, root=False, attr_type=False)
        envio_dte = self._envio_recep(caratula, receipts)
        envio_dte = self.inv.sign_full_xml(
            envio_dte,
            signature_d['priv_key'],
            certp,
            'SetDteRecibidos',
            'env_recep')
        if self.inv:
            self.inv.sii_receipt = envio_dte
        att = self._create_attachment(envio_dte, 'recepcion_mercaderias_' + str(self.inv.sii_send_file_name))
        self.inv.message_post(
            body='XML de Recepción de Documeto\n %s' % (message),
            subject='XML de Recepción de Documento',
            partner_ids=[ self.inv.partner_id.id ],
            attachment_ids=[ att.id ],
            message_type='comment',
            subtype='mt_comment')

    def _create_partner(self, data):
        giro_id = self.env['sii.activity.description'].search([('name','=',data['GiroEmis'])])
        if not giro_id:
            giro_id = self.env['sii.activity.description'].create({
                'name': data['GiroEmis'],
            })
        rut = self.format_rut(data['RUTEmisor'])
        partner_id = self.env['res.partner'].create({
            'name': data['RznSoc'],
            'activity_description': giro_id.id,
            'vat': rut,
            'document_type_id': self.env.ref('l10n_cl_invoice.dt_RUT').id,
            'responsability_id': self.env.ref('l10n_cl_invoice.res_IVARI').id,
            'document_number': data['RUTEmisor'],
            'street': data['DirOrigen'],
            'city':data['CiudadOrigen'],
            'company_type':'company',
            'supplier': True,
        })
        return partner_id

    def _default_category(self,):
        md = self.env['ir.model.data']
        res = False
        try:
            res = md.get_object_reference('product', 'product_category_all')[1]
        except ValueError:
            res = False
        return res

    def _create_prod(self, data):
        product_id = self.env['product.product'].create({
            'sale_ok':False,
            'name': data['NmbItem'],
            'lst_price': float(data['PrcItem'] if 'PrcItem' in data else data['MontoItem']),
            'categ_id': self._default_category(),
        })
        if 'CdgItem' in data:
            if 'TpoCodigo' in data['CdgItem']:
                if data['CdgItem']['TpoCodigo'] == 'ean13':
                    product_id.barcode = data['CdgItem']['VlrCodigo']
                else:
                    product_id.default_code = data['CdgItem']['VlrCodigo']
            else:
                for c in data['CdgItem']:
                    if c['TpoCodigo'] == 'ean13':
                        product_id.barcode = c['VlrCodigo']
                    else:
                        product_id.default_code = c['VlrCodigo']
        return product_id

    def _buscar_producto(self, line):
        query = product_id = False
        if 'CdgItem' in line:
            if 'VlrCodigo' in line['CdgItem']:
                if line['CdgItem']['TpoCodigo'] == 'ean13':
                    query = [('barcode','=',line['CdgItem']['VlrCodigo'])]
                else:
                    query = [('default_code','=',line['CdgItem']['VlrCodigo'])]
            else:
                for c in line['CdgItem']:
                    if line['CdgItem']['TpoCodigo'] == 'ean13':
                        query = [('barcode','=',c['VlrCodigo'])]
                    else:
                        query = [('default_code','=',c['VlrCodigo'])]
        if not query:
            query = [('name','=',line['NmbItem'])]
        product_id = self.env['product.product'].search(query)
        if not product_id:
            product_id = self._create_prod(line)
        return product_id

    def _prepare_line(self, line, journal, type):
        product_id = self._buscar_producto(line)

        account_id = journal.default_debit_account_id.id
        if type in ('out_invoice', 'in_refund'):
                account_id = journal.default_credit_account_id.id
        if 'MntExe' in line:
            price_subtotal = price_included = float(line['MntExe'])
        else :
            price_subtotal = float(line['MontoItem'])
        discount = 0
        if 'DescuentoPct' in line:
            discount = line['DescuentoPct']
        return [0,0,{
            'name': line['DescItem'] if 'DescItem' in line else line['NmbItem'],
            'product_id': product_id.id,
            'price_unit': line['PrcItem'] if 'PrcItem' in line else price_subtotal,
            'discount': discount,
            'quantity': line['QtyItem'] if 'QtyItem' in line else 1,
            'account_id': account_id,
            'price_subtotal': price_subtotal,
            'invoice_line_tax_ids': [(6, 0, product_id.supplier_taxes_id.ids)],
        }]

    def _prepare_ref(self, ref):
        try:
            tpo = self.env['sii.document_class'].search([('sii_code', '=', ref['TpoDocRef'])])
        except:
            tpo = self.env['sii.document_class'].search([('sii_code', '=', 801)])
        if not tpo:
            raise UserError(_('No existe el tipo de documento'))
        folio = ref['FolioRef']
        fecha = ref['FchRef']
        cod_ref = ref['CodRef'] if 'CodRef' in ref else None
        motivo = ref['RazonRef'] if 'RazonRef' in ref else None
        return [0,0,{
        'origen' : folio,
        'sii_referencia_TpoDocRef' : tpo.id,
        'sii_referencia_CodRef' : cod_ref,
        'motivo' : motivo,
        'fecha_documento' : fecha,
        }]

    def _prepare_invoice(self, dte, company_id, journal_document_class_id):
        partner_id = self.env['res.partner'].search([
        ('active','=', True),
        ('parent_id', '=', False),
        ('vat','=', self.format_rut(dte['Encabezado']['Emisor']['RUTEmisor']))])
        if not partner_id:
            partner_id = self._create_partner(dte['Encabezado']['Emisor'])
        elif not partner_id.supplier:
            partner_id.supplier = True
        name = self.filename.decode('ISO-8859-1').encode('UTF-8')
        xml =base64.b64decode(self.xml_file).decode('ISO-8859-1')
        return {
            'origin' : 'XML Envío: ' + name,
            'reference': dte['Encabezado']['IdDoc']['Folio'],
            'date_invoice' :dte['Encabezado']['IdDoc']['FchEmis'],
            'partner_id' : partner_id.id,
            'company_id' : company_id.id,
            'account_id': partner_id.property_account_payable_id.id,
            'journal_id': journal_document_class_id.journal_id.id,
            'turn_issuer': company_id.company_activities_ids[0].id,
            'journal_document_class_id':journal_document_class_id.id,
            'sii_xml_request': xml ,
            'sii_send_file_name': name,
        }

    def _get_journal(self, sii_code, company_id):
        journal_sii = self.env['account.journal.sii_document_class'].search(
                [('sii_document_class_id.sii_code', '=', sii_code),
                ('journal_id.type','=','purchase'),
                ('journal_id.company_id', '=', company_id.id)
                ],
                limit=1,
            )
        return journal_sii

    def _create_inv(self, dte, company_id):
        inv = self.env['account.invoice'].search(
        [
            ('reference','=',dte['Encabezado']['IdDoc']['Folio']),
            ('type','in',['in_invoice','in_refund']),
            ('sii_document_class_id.sii_code','=',dte['Encabezado']['IdDoc']['TipoDTE']),
            ('partner_id.vat','=', self.format_rut(dte['Encabezado']['Emisor']['RUTEmisor'])),
        ])
        if not inv:
            company_id = self.env['res.company'].search([
                ('vat','=', self.format_rut(dte['Encabezado']['Receptor']['RUTRecep']))])
            journal_document_class_id = self._get_journal(dte['Encabezado']['IdDoc']['TipoDTE'], company_id)
            if not journal_document_class_id:
                sii_document_class = self.env['sii.document_class'].search([('sii_code', '=', dte['Encabezado']['IdDoc']['TipoDTE'])])
                raise UserError('No existe Diario para el tipo de documento %s, por favor añada uno primero, o ignore el documento' % sii_document_class.name.encode('UTF-8'))
            data = self._prepare_invoice(dte, company_id, journal_document_class_id)
            data['type'] = 'in_invoice'
            if dte['Encabezado']['IdDoc']['TipoDTE'] in ['54', '61']:
                data['type'] = 'in_refund'
            lines = [(5,)]
            if 'NroLinDet' in dte['Detalle']:
                lines.append(self._prepare_line(dte['Detalle'], journal=journal_document_class_id.journal_id, type=data['type']))
            elif len(dte['Detalle']) > 0:
                for line in dte['Detalle']:
                    lines.append(self._prepare_line(line, journal=journal_document_class_id.journal_id, type=data['type']))
            refs = []
            if 'Referencia' in dte:
                refs = [(5,)]
                if 'NroLinRef' in dte['Referencia']:
                    refs.append(self._prepare_ref(dte['Referencia']))
                else:
                    for ref in dte['Referencia']:
                        refs.append(self._prepare_ref(ref))
            data['invoice_line_ids'] = lines
            data['referencias'] = refs
            inv = self.env['account.invoice'].create(data)
            monto_xml = float(dte['Encabezado']['Totales']['MntTotal'])
            if inv.amount_total == monto_xml:
                return inv
            #cuadrar en caso de descuadre por 1$
            #if (inv.amount_total - 1) == monto_xml or (inv.amount_total + 1) == monto_xml:
            inv.amount_total = monto_xml
            for t in inv.tax_line_ids:
                if t.tax_id.amount == float(dte['Encabezado']['Totales']['TasaIVA']):
                    t.amount = float(dte['Encabezado']['Totales']['IVA'])
                    t.base = float(dte['Encabezado']['Totales']['MntNeto'])
            #else:
            #    raise UserError('¡El documento está completamente descuadrado!')
        return inv

    def do_create_inv(self):
        envio = self._read_xml('parse')
        resp = self.do_receipt_deliver()
        if 'Documento' in envio['EnvioDTE']['SetDTE']['DTE']:
            dte = envio['EnvioDTE']['SetDTE']['DTE']
            company_id = self.env['res.company'].search(
                [
                    ('vat','=', self.format_rut(dte['Documento']['Encabezado']['Receptor']['RUTRecep'])),
                ],
                limit=1)
            if company_id:
                self.inv = self._create_inv(dte['Documento'], company_id)
                #if self.inv:
                #    self.inv.sii_xml_response = resp['warning']['message']
        else:
            for dte in envio['EnvioDTE']['SetDTE']['DTE']:
                company_id = self.env['res.company'].search(
                    [
                        ('vat','=', self.format_rut(dte['Documento']['Encabezado']['Receptor']['RUTRecep'])),
                    ],
                    limit=1)
                if company_id:
                    self.inv = self._create_inv(dte['Documento'], company_id)
                #    if self.inv:
                #        self.inv.sii_xml_response = resp['warning']['message']
        if not self.inv:
            raise UserError('El archivo XML no contiene documentos para alguna empresa registrada en Odoo, o ya ha sido procesado anteriormente ')
        return resp

    def _create_po(self, dte):
        partner_id = self.env['res.partner'].search([
        ('active','=', True),
        ('parent_id', '=', False),
        ('vat','=', self.format_rut(dte['Encabezado']['Emisor']['RUTEmisor']))])
        if not partner_id:
            partner_id = self._create_partner(dte['Encabezado']['Emisor'])
        elif not partner_id.supplier:
            partner_id.supplier = True
        company_id = self.env['res.company'].search([
            ('vat','=', self.format_rut(dte['Encabezado']['Receptor']['RUTRecep']))
            ])
        data = {
            'partner_ref' : dte['Encabezado']['IdDoc']['Folio'],
            'date_order' :dte['Encabezado']['IdDoc']['FchEmis'],
            'partner_id' : partner_id.id,
            'company_id' : company_id.id,
        }
        lines =[(5,)]
        for line in dte['Detalle']:
            product_id = self.env['product.product'].search([('name','=',line['NmbItem'])])
            if not product_id:
                product_id = self._create_prod(line)
            lines.append([0,0,{
                'name': line['DescItem'] if 'DescItem' in line else line['NmbItem'],
                'product_id': product_id,
                'product_qty': line['QtyItem'],
            }])
        data['order_lines'] = lines
        po = self.env['purchase.order'].create(data)
        po.button_confirm()
        self.inv = self.env['account.invoice'].search([('purchase_id', '=', po.id)])
        #inv.sii_document_class_id = dte['Encabezado']['IdDoc']['TipoDTE']
        return po

    def do_create_po(self):
        #self.validate()
        envio = self._read_xml('parse')
        for dte in envio['EnvioDTE']['SetDTE']['DTE']:
            if dte['TipoDTE'] in ['34', '33']:
                self._create_po(dte['Documento'])
            elif dte['56','61']: # es una nota
                self._create_inv(dte['Documento'])
