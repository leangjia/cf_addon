# -*- coding: utf-8 -*-
#################################################################################
#
#    Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#
#################################################################################
from odoo.addons.web.controllers.main import Home
import json
import urlparse
import string
import xml.etree.ElementTree as ET
import werkzeug
from odoo import _
from odoo.http import request, Controller, route
import logging
_logger = logging.getLogger(__name__)
from base64 import b64decode
from ast import literal_eval
from functools import wraps
from odoo.addons.mobikul.models.mobikul import _displayWithCurrency, _get_image_url
import hashlib
import requests
from ast import literal_eval

# from werkzeug.http import parse_authorization_header
# from odoo.http import Controller, dispatch_rpc
import re
TAG_RE = re.compile(r'<[^>]+>')

def remove_htmltags(text):
	return TAG_RE.sub('', text)


PRICE_FIELDS = [
"amount_total","amount_untaxed","amount_tax",
"price_unit","price_subtotal","price_tax","price_total","price"
]

AQUIRER_REF_CODES = [
'COD','STRIPE_E','STRIPE_W',
	"2_CHECKOUT","PAYFORT_SADAD","PAYFORT"
]
# For UI Controller payment Aquirer
# Note: <For line no:1056> Remenber in place order api (def placeorder) methods Acquire name for Ui controller gateway like '2_CHECKOUT' and 'PAYFORT_SADAD' should be same as in backend acquire name.

PAYFORT_SDK_ENV_URL = {
"test":"https://sbpaymentservices.payfort.com/FortAPI/paymentApi",
"prod":"https://paymentservices.payfort.com/FortAPI/paymentApi"
}

STATUS_MAPPING = {
"STRIPE": {'succeeded':'done','pending':'pending','failed':'error'},
}

import uuid
def _get_next_reference(order_name):
	return order_name+"-"+str(uuid.uuid4())[:5]

def _computeOfferString(offer):

	#Order Level Discount
	old_discount = offer.order_discount
	old_min_amount = offer.min_order_amt_so_discount
	old_max_amount = offer.max_discount_for_so

	#Payment Method Discount
	pmd_discount = offer.payment_acquirer_discount
	pmd_min_amount = offer.min_order_amt_payment_acq_discount
	pmd_max_amount = offer.max_discount_for_payment_acq
	arr = ["%s%% extra discount on order above %s (Maximum discount %s)."%(old_discount,old_min_amount,old_max_amount)]
	if offer.payment_acquirer_id:
		arr.append("%s%% extra discount on order above %s by paying through %s."%(pmd_discount,pmd_min_amount,offer.payment_acquirer_id.name))
	# return {"offer":arr}
	return arr


def _computeProductOffer(TemplateObj):
	# array product offers
	bonus = []
	bonus_ids = TemplateObj.bogo_offer_rule_ids
	for b in bonus_ids:
		bonus.append("%s %s Bonus of %s on minimum quantity %s"%(b.free_qty, b.free_qty_type == "percentage" and "%" or "fixed", b.product_id.name, b.min_ordered_qty))
	return bonus

class xml(object):

	@staticmethod
	def _encode_content(data):
		# .replace('&', '&amp;')
		return data.replace('<','&lt;').replace('>','&gt;').replace('"', '&quot;')

	@classmethod
	def dumps(cls, apiName, obj):
		_logger.warning("%r : %r"%(apiName, obj))
		if isinstance(obj, dict):
			return "".join("<%s>%s</%s>" % (key, cls.dumps(apiName, obj[key]), key) for key in obj)
		elif isinstance(obj, list):
			return "".join("<%s>%s</%s>" % ("I%s" % index, cls.dumps(apiName, element),"I%s" % index) for index,element in enumerate(obj))
		else:
			return "%s" % (xml._encode_content(obj.__str__()))

	@staticmethod
	def loads(string):
		def _node_to_dict(node):
			if node.text:
				return node.text
			else:
				return {child.tag: _node_to_dict(child) for child in node}
		root = ET.fromstring(string)
		return {root.tag: _node_to_dict(root)}

class WebServices(Controller):

	def __decorateMe(func):
		@wraps(func)
		def wrapped(inst, *args, **kwargs):
			inst._mData = request.httprequest.data and json.loads(request.httprequest.data) or {}
			inst._mAuth = request.httprequest.authorization and (request.httprequest.authorization.get('password') or request.httprequest.authorization.get("username")) or None
			inst.base_url = request.httprequest.host_url
			inst._lcred = {}
			inst._sLogin = False
			inst.auth = True
			inst._mLang = request.httprequest.headers.get("lang") or None
			if request.httprequest.headers.get("Login"):
				try:
					inst._lcred = literal_eval(b64decode(request.httprequest.headers["Login"]))
				except:
					inst._lcred = {"login":None,"pwd":None}
			elif request.httprequest.headers.get("SocialLogin"):
				inst._sLogin = True
				try:
					inst._lcred = literal_eval(b64decode(request.httprequest.headers["SocialLogin"]))
				except:
					inst._lcred = {"authProvider":1,"authUserId":1234567890}
			else:
				inst.auth = False
			return func(inst, *args, **kwargs)
		return wrapped

	def _available_api(self):
		API = {
		'homepage':{
					'description':'HomePage API',
					'uri':'/mobikul/homepage'
				},
		'sliderProducts':{
					'description':'Product(s) of given Product Slider Record',
					'uri':'/mobikul/sliderProducts/&lt;int:product_slider_id&gt;',
				},
		'login':{
					'description':'Customer Login',
					'uri':'/mobikul/customer/login',
				},
		'signUp':{
					'description':'Customer signUp',
					'uri':'/mobikul/customer/signUp',
				},
		'resetPassword':{
					'description':'Customer Reset Password',
					'uri':'/mobikul/customer/resetPassword',
				},
		'splashPageData':{
					'description':'Default data to saved at app end.',
					'uri':'/mobikul/splashPageData',
				},
		}
		return API

	def _wrap2xml(self, apiName, data):
		resp_xml = "<?xml version='1.0' encoding='UTF-8'?>"
		resp_xml += '<odoo xmlns:xlink="http://www.w3.org/1999/xlink">'
		resp_xml += "<%s>"%apiName
		resp_xml += xml.dumps(apiName, data)
		resp_xml += "</%s>"%apiName
		resp_xml += '</odoo>'
		return resp_xml

	def _response(self, apiName, response, ctype='json'):
		if response.has_key("local"):
			response.pop("local")
		if ctype=='json':
			mime='application/json; charset=utf-8'
			body = json.dumps(response)
		else:
			mime='text/xml'
			body = self._wrap2xml(apiName,response)
		headers = [
					('Content-Type', mime),
					('Content-Length', len(body))
				]
		return werkzeug.wrappers.Response(body, headers=headers)

	@__decorateMe
	def _authenticate(self, auth, **kwargs):
		if kwargs.has_key('api_key'):
			api_key  = kwargs.get('api_key')
		elif request.httprequest.authorization:
			api_key  = request.httprequest.authorization.get('password') or request.httprequest.authorization.get("username")
		else:
			api_key = False

		Mobikul = request.env['mobikul'].sudo()
		response = Mobikul._validate(api_key,{"lang":self._mLang})
		if not response.get('success'):
			return response
		request.context = dict(request.context, pricelist=response.get('pricelist'), lang=response.get('lang'), base_url=self.base_url)
		if auth:
			Mobikul = request.env['mobikul'].sudo()
			result = Mobikul.authenticate(self._lcred, kwargs.get('detailed',False),self._sLogin, context={'base_url':self.base_url})
			response.update(result)
		return response

	@route('/mobikul/', csrf=False, type='http', auth="none")
	def index(self, **kwargs):
		""" HTTP METHOD : request.httprequest.method
		"""
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			data = self._available_api()
			return self._response('mobikulApi', data, 'xml')
		else:
			headers=[('WWW-Authenticate','Basic realm="Welcome to Odoo Webservice, please enter the authentication key as the login. No password required."')]
			return werkzeug.wrappers.Response('401 Unauthorized %r'%request.httprequest.authorization, status=401, headers=headers)

	@route('/mobikul/homepage', csrf=False, type='http', auth="none", methods=['POST'])
	def getHomepage(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			response.update(self._languageData())
			Mobikul = request.env['mobikul'].sudo()
			if self.auth:
				result = Mobikul.authenticate(self._lcred, True, self._sLogin, context={'base_url':self.base_url})
				response.update(result)
			local = response.get('local',{})
			context = {"base_url":self.base_url, "currencySymbol":local.get("currencySymbol",""),
							"currencyPosition":local.get("currencyPosition",""),"lang_obj":local.get("lang_obj","")}
			result = Mobikul.homePage(self._mData,context)
			response.update(result)
			self._tokenUpdate(customer_id=response.get('customerId'))
			response.update({"notificationCount":self._countNotification(customer_id=response.get('customerId'))})
		return self._response('homepage', response)

	def _countNotification(self,customer_id):
		count = 0
		if customer_id :
			count = request.env['mobikul.notification.messages'].sudo().search_count([('customer_id','=',int(customer_id)),('is_read','=',False)])
		return count

	@route(['/mobikul/sliderProducts/<int:slider_id>'], type='http', auth="none", csrf=False, methods=['GET','POST'])
	def getSliderProducts(self, slider_id, **kwargs):
		if request.httprequest.headers.get("Login"):
			response = self._authenticate(True, **kwargs)
		else:
			response = self._authenticate(False, **kwargs)
		if response.get('addons',{}).get('wishlist') and response.get('customerId'):
			response['wishlist']= self._myWishlist(response.get('customerId'))
		if response.get('success'):
			PSlider = request.env['mobikul.product.slider'].sudo().search([('id','=',slider_id)])
			if PSlider:
				local = response.get('local',{})
				context = {
						'limit':response.get('itemsPerPage',5),
						'offset':self._mData.get('offset',0),
						'order':self._mData.get('order',None),
						"currencySymbol":local.get("currencySymbol",""),
						"currencyPosition":local.get("currencyPosition",""),
						'lang_obj':local.get("lang_obj",""),
					}
				result = PSlider.get_product_data(context)
			else:
				result = {'success':False, 'message':'Product Slider not found !!!'}
			response.update(result)
		return self._response('sliderProducts', response)

	@route('/mobikul/customer/login', csrf=False, type='http', auth="none", methods=['POST'])
	def login(self, **kwargs):
		kwargs['detailed'] = True
		response = self._authenticate(True, **kwargs)
		self._tokenUpdate(customer_id=response.get('customerId'))
		return self._response('login', response)

	@route('/mobikul/customer/signUp', csrf=False, type='http', auth="none", methods=['POST'])
	def signUp(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			createNotification = False
			Mobikul = request.env['mobikul'].sudo()
			result = Mobikul.signUp(self._mData)
			response.update(result)
			if response['success'] and response.get('addons',{}).get('email_verification') and Mobikul.email_verification_defaults().get('send_email_on_signup'):
				if not self._mData.get('authUserId',False):
					response["message"] = _("An email has been sent to your email address. Please verify it.")
					createNotification = True
			if response['success']:
				homepage = {}
				login = {}
				if self._mData.get('authUserId',False):
					cred = {'authUserId':self._mData.get('authUserId',""),'authProvider':self._mData.get('authProvider',"")}
				else:
					cred = {'login':self._mData.get('login',""),'pwd':self._mData.get('password',"")}
				login = Mobikul.authenticate(cred, True, self._sLogin, context={'base_url':self.base_url})
				response.update({"login":login,"cred":cred})
				local = response.get('local',{})
				context = {
						"base_url":self.base_url,
						"currencySymbol":local.get("currencySymbol",""),
						"currencyPosition":local.get("currencyPosition",""),
						"lang_obj":local.get('lang_obj'),
						}
				result = Mobikul.homePage(self._mData,context)
				homepage.update(result)
				response.update({"homepage":homepage})
			self._tokenUpdate(customer_id=response.get('customerId'))
			if response.get("message","").startswith("Created"):
				createNotification = True
			if createNotification:
				self._pushNotification( self._mData.get("fcmToken",""), customer_id = response.get('customerId') )
		return self._response('signUp', response)

	@route('/mobikul/customer/resetPassword', csrf=False, type='http', auth="none", methods=['POST'])
	def resetPassword(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			Mobikul = request.env['mobikul'].sudo()
			result = Mobikul.resetPassword(self._mData.get('login',False))
			response.update(result)
		return self._response('resetPassword', response)

	@route('/mobikul/customer/signOut', csrf=False, type='http', auth="none", methods=['POST'])
	def signOut(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			response['message'] = "Have a Good Day !!!"
			self._tokenUpdate()
		return self._response('signOut', response)

	@route('/mobikul/splashPageData', csrf=False, type='http', auth="none", methods=['POST'])
	def getSplashPageData(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			Mobikul = request.env['mobikul'].sudo()
			if self._lcred.has_key('login'):
				result = Mobikul.authenticate(self._lcred, True,self._sLogin, context={'base_url':self.base_url})
				response.update(result)
			result = Mobikul.getDefaultData()
			response.update(result)
			response['sortData'] = [
				("Price: High to Low", "price desc"),
				("Price: Low to High", "price asc"),
				("Discounts", "id asc"),
				("Popularity", "id asc"),
				("Newest First", "id desc"),
			]
			response.update(self._languageData())
			if response.get('addons',{}).get('review'):
				response['RatingStatus'] = [
					("1",_("Poor")),
					("2",_("Ok")),
					("3",_("Good")),
					("4",_("Very Good")),
					("5",_("Excellent")),
				]
			response.update({
								"signupData":{
											"job_title":  [
															_("Pharmacy Owner"),
															_("Pharmacy Manager"),
															_("Pharmacist"),
															_("Assistant Pharmacist"),
															_("Pharmacy Student")
															],
											'placeholder':_("Job Title"),
											},

						})
		return self._response('splashPageData', response)

	def _languageData(self):
		mobikul = request.env['mobikul'].sudo().search([], limit=1)
		temp = {
				'defaultLanguage':(mobikul.default_lang.code,mobikul.default_lang.name),
				'allLanguages':[(id.code,id.name) for id in mobikul.language_ids ]
		}

		return temp

	@route('/mobikul/my/orders', csrf=False, type='http', auth="none", methods=['POST'])
	def getMyOrders(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner:
				result = {}
				local = response.get('local',{})
				fields = ['name', 'create_date', 'state', 'amount_total', 'partner_shipping_id']
				SaleOrder = request.env['sale.order'].sudo()

				domain = [
						('message_partner_ids', 'child_of', [Partner.commercial_partner_id.id]),
						('state','not in',('draft','sent'))
					]
				if self._mData.get('date_from',False) and self._mData.get('date_to',False):
					# domain += [('create_date', '>', context['date_from']), ('create_date', '<=', context['date_to'])]
					domain += [('create_date', '>',self._mData.get('date_from') ), ('create_date', '<=', self._mData.get('date_to'))]

				result['tcount'] = SaleOrder.search_count(domain)
				orders = SaleOrder.search_read(domain, limit=self._mData.get('limit',response.get('itemsPerPage',5)), offset=self._mData.get('offset',0),  order="id desc", fields=fields)
				result['recentOrders'] = []
				for order in orders:
					ShippingAdd = PartnerObj.search([('id','=',order['partner_shipping_id'][0])])
					temp = {
					'id':order['id'],
					'name':order['name'] or "",
					'create_date':order['create_date'],
					'shipping_address':ShippingAdd and ShippingAdd._display_address() or "",
					'shipAdd_url':ShippingAdd and '/mobikul/my/address/%s'%ShippingAdd.id or "",
					'amount_total':_displayWithCurrency(local.get('lang_obj'),order['amount_total'], local.get('currencySymbol',""), local.get('currencyPosition',"")),
					'status':order['state'],
					'canReorder':True,
					'url':"/mobikul/my/order/%s"%order['id'],
					}
					result['recentOrders'].append(temp)
			else:
				result = {'success':False, 'message':'Customer not found !!!'}
			response.update(result)
		return self._response('orders', response)

	@route('/mobikul/my/order/<int:order_id>', csrf=False, type='http', auth="none", methods=['POST'])
	def getMyOrder(self, order_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			Order = request.env['sale.order'].sudo().search([('id','=',order_id)])
			if Order:
				local = response.get('local',{})
				result = {
					'name':Order.name or "",
					'create_date':Order.create_date or "",
					'amount_total':_displayWithCurrency(local.get('lang_obj'),Order.amount_total, local.get('currencySymbol',""), local.get('currencyPosition',"")),
					'status':Order.state,
					'amount_untaxed':_displayWithCurrency(local.get('lang_obj'),Order.amount_untaxed, local.get('currencySymbol',""), local.get('currencyPosition',"")),
					'amount_tax':_displayWithCurrency(local.get('lang_obj'),Order.amount_tax, local.get('currencySymbol',""), local.get('currencyPosition',"")),
					'shipping_address':Order.partner_shipping_id._display_address(),
					'shipAdd_url':'/mobikul/my/address/%s'%Order.partner_shipping_id.id,
					'billing_address':Order.partner_invoice_id._display_address(),
				}
				result['items'] = []
				for line in Order.order_line:
					if response.get('addons', {}).get('website_sale_delivery') and line.is_delivery:
						shippingMethod = {
					      	"tax":[tax.name for tax in line.tax_id],
							"name":line.order_id.carrier_id.name,
							"description":line.order_id.carrier_id.website_description or "",
							"shippingId":line.order_id.carrier_id.id,
							"total": _displayWithCurrency(local.get('lang_obj'), line.price_subtotal,
														  local.get('currencySymbol'), local.get('currencyPosition')),
						}
						result.update({"delivery":shippingMethod})
					else:
						temp = {
						'name':line.name or "",
						'product_name':line.product_id and line.product_id.display_name or "",
						'qty':"%s %s"%(line.product_uom_qty, line.product_uom.name),
						'price_unit':_displayWithCurrency(local.get('lang_obj'),line.price_unit, local.get('currencySymbol',""), local.get('currencyPosition',"")),
						'price_subtotal':_displayWithCurrency(local.get('lang_obj'),line.price_subtotal, local.get('currencySymbol',""), local.get('currencyPosition',"")),
						'price_tax':_displayWithCurrency(local.get('lang_obj'),line.price_tax, local.get('currencySymbol',""), local.get('currencyPosition',"")),
						'price_total':_displayWithCurrency(local.get('lang_obj'),line.price_total, local.get('currencySymbol',""), local.get('currencyPosition',"")),
						# 'discount':"%s"%(line.discount and "%s %"%line.discount or ""),
						'discount':line.discount and line.discount or "",
						"max_bonus": line.product_id.max_percentage_bonus ,
						'state':line.state,
						'thumbNail'		:_get_image_url(self.base_url, 'product.product', line.product_id and line.product_id.id or "",'image'),
						"templateId":line.product_id and line.product_id.product_tmpl_id.id or "",
						}
						result['items'].append(temp)
			else:
				result = {'success':False, 'message':'Order not found !!!'}
			response.update(result)
		return self._response('orders', response)

	@route('/mobikul/my/addresses', csrf=False, type='http', auth="none", methods=['POST'])
	def getMyAddresses(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner:
				result = {}
				domain = [
						('id', 'child_of', Partner.commercial_partner_id.ids),
						('id', 'not in', [Partner.id]),
					]
				result['tcount'] = PartnerObj.search_count(domain) + 1
				addresses = PartnerObj.search(domain, limit=self._mData.get('limit',response.get('itemsPerPage',5)), offset=self._mData.get('offset',0),  order="id desc")
				result['addresses'] = [
					{
						'name':Partner.name,
						'display_name':Partner._display_address(),
						'url':"/mobikul/my/address/%s"%Partner.id,
						'addressId':Partner.id,
					}
				]
				# in result['addresses'][0] zero index address is billing address other is shipping address
				for address in addresses:
					temp = {
					'name':address.name,
					'display_name':address._display_address(),
					'url':"/mobikul/my/address/%s"%address.id,
					'addressId':address.id,
					}
					result['addresses'].append(temp)
			else:
				result = {'success':False, 'message':'Customer not found !!!'}
			response.update(result)
		return self._response('orders', response)

	@route('/mobikul/my/address/default/<int:address_id>', csrf=False, type='http', auth="none", methods=['PUT'])
	def setDefaultAddress(self, address_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Address = PartnerObj.search([('id','=',address_id)])
			if Address:
				result = {'message':'Updated successfully.'}
			else:
				result = {'success':False, 'message':'Address not found !!!'}
			response.update(result)
		return self._response('address', response)

	@route('/mobikul/my/address/new', csrf=False, type='http', auth="none", methods=['POST'])
	def addMyAddress(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			vals = {
				"name":self._mData.get('name',""),
				"street":self._mData.get('street',""),
				# "street2":self._mData.get('street2',""),
				"zip":self._mData.get('zip',""),
				"city":self._mData.get('city',""),
				"phone":self._mData.get('phone',""),
				"customer":1,
				"type":"delivery",
				"commercial_partner_id":int(response.get('customerId')),
				"parent_id":int(response.get('customerId')),
			}
			try:
				if self._mData.get("state_id"):
					if request.env['res.country.state'].sudo().browse(int(self._mData["state_id"])).exists():
						vals["state_id"] = int(self._mData["state_id"])
				if self._mData.get("country_id"):
					if request.env['res.country'].sudo().browse(int(self._mData["country_id"])).exists():
						vals["country_id"] = int(self._mData["country_id"])
				PartnerObj.create(vals)
				result = {'message':'Created successfully.'}
			except Exception,e:
				result = {'success':False, 'message':'Error: Invalid Data'}
			response.update(result)
		return self._response('address', response)

	@route('/mobikul/my/address/<int:address_id>', csrf=False, type='http', auth="none", methods=['POST','PUT','DELETE'])
	def getMyAddress(self, address_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Address = PartnerObj.search([('id','=',address_id)])
			if Address:
				if request.httprequest.method in ["POST"]:
					result = {
						'name':Address.name or "" ,
						'street':Address.street or "",
						# 'street2':Address.street2 or "",
						'zip':Address.zip or "",
						'city':Address.city or "",
						'state_id':Address.state_id and Address.state_id.id or "",
						'country_id':Address.country_id and Address.country_id.id or "",
						'phone':Address.phone or "",
						# 'fax':Address.fax,
						# 'mobile':Address.mobile,
					}
				elif request.httprequest.method == "PUT":
					Address.name = self._mData.get('name',Address.name)
					Address.street = self._mData.get('street',Address.street)
					# Address.street2 = self._mData.get('street2',Address.street2)
					Address.zip = self._mData.get('zip',Address.zip)
					Address.city = self._mData.get('city',Address.city)
					Address.phone = self._mData.get('phone',Address.phone)
					try:
						if self._mData.get("state_id"):
							if request.env['res.country.state'].sudo().browse(int(self._mData["state_id"])).exists():
								Address.state_id = int(self._mData["state_id"])
						if self._mData.get("country_id"):
							if request.env['res.country'].sudo().browse(int(self._mData["country_id"])).exists():
								Address.country_id = int(self._mData["country_id"])
						result = {'message':'Updated successfully.'}
					except Exception,e:
						result = {'success':False, 'message':'Error: Invalid Data'}
				elif request.httprequest.method == "DELETE":
					if response.get('customerId') != address_id:
						Address.active = False
						result = {'message':'Deleted successfully.'}
					else:
						result = {'success':False, 'message':_('Error: You can`t delete Billing Address.')}
			else:
				result = {'success':False, 'message':'Address not found !!!'}
			response.update(result)
		return self._response('address', response)

	@route('/mobikul/my/account', csrf=False, type='http', auth="none", methods=['POST'])
	def getMyAccount(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner:
				result = {}
				result['data'] = {
				'name': {'required':True, 'label':_("Your name"), 'value':Partner.name or ""},
				'email': {'required':True, 'readonly':True, 'label':_("Email"), 'value':Partner.email or ""},
				'phone': {'label':_("Phone"), 'value':Partner.phone or ""},
				'street': {'label':_("Street"), 'value':Partner.street or ""},
				'street2': {'label':_("Street2"), 'value':Partner.street2 or ""},
				'city': {'label':_("City"), 'value':Partner.city or ""},
				'zip': {'label':_("Zip / Postal Code"), 'value':Partner.zip or ""},
				'country_id': {'label':_("Country"), 'value':Partner.country_id and Partner.country_id.id or ""},
				'state_id': {'label':_("State"), 'value':Partner.state_id and Partner.state_id.id or ""},
				}
			else:
				result = {'success':False, 'message':'Account not found !!!'}
			response.update(result)
		return self._response('account', response)

	@route('/mobikul/localizationData', csrf=False, type='http', auth="none", methods=['POST'])
	def getLocalizationData(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		Mobikul = request.env['mobikul'].sudo()
		if response.get('success'):
			StateObj = request.env['res.country.state'].sudo()
			countries = Mobikul.fetch_countries()
			if countries:
				result = {'countries':[]}
				state_ids = []
				for country in countries:
					states = []
					if country['state_ids']:
						states = StateObj.search_read([('id','in',country['state_ids'])], fields=['name'])
					result['countries'].append({
							'id':country['id'],
							'name':country['name'],
							'states':states,
							})
			else:
				result = {'success':False, 'message':'Account not found !!!'}
			response.update(result)
		return self._response('account', response)

	@route('/mobikul/search', csrf=False, type='http', auth="none", methods=['POST'])
	def getSearchData(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		Mobikul = request.env['mobikul'].sudo()
		if self.auth:
			result = Mobikul.authenticate(self._lcred, True, self._sLogin, context={'base_url':self.base_url})
			response.update(result)
		if response.get('success'):
			self._mData.update(response.get('local',{}))
			result = Mobikul.fetch_products(**self._mData)
			response.update(result)
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				response['wishlist']= self._myWishlist(response.get('customerId'))
		return self._response('search', response)

	def _getOfferTerms(self,TemplateObj,seller_id):
		sale_offer = request.env['sale.offer'].sudo().search([('marketplace_seller_id', '=', seller_id)])
		offers = []
		product_offer = _computeProductOffer(TemplateObj)
		if product_offer:
			offers.append(product_offer)
		for off in sale_offer:
			offers.append(_computeOfferString(off))
		return offers

	@route('/mobikul/template/<int:template_id>', csrf=False, type='http', auth="none", methods=['POST'])
	def getTemplateData(self, template_id, **kwargs):
		response = self._authenticate(False, **kwargs)
		if self.auth:
			result = request.env['mobikul'].sudo().authenticate(self._lcred, True, self._sLogin, context={'base_url':self.base_url})
			response.update(result)
		if response.get('success'):
			wishlist = []
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				wishlist = self._myWishlist(response.get('customerId',0))
			TemplateObj = request.env['product.template'].sudo()
			Template = TemplateObj.search([('id','=',template_id)])
			if Template:
				result = {
					'templateId'	:Template.id,
					'name'			:Template.name or "",
					'attributes'	:[],
					"images": [],

					"barcode": Template.barcode,
					"categ_name": Template.categ_id.name,

					# smart_pharmacy_codefish
					"manufacutre":Template.manufacture.name,

					# website_sale_offer
					"discount"  : Template.pharmacy_discount,
					"max_bonus"  : Template.max_percentage_bonus,


					# marketplace_theme_pharmacistplace_compatibility
					"in_stock"  : Template.custom_stock_status,

					# pharmacy_product_tags
					"tags": [tag.name for tag in  Template.pharmacy_tag_ids]



				}
				local = response.get('local',{})
				if response.get('addons',{}).get('review'):
					result.update({
						'avg_rating':Template.avg_review(),
						'total_review':len(Template.fetch_active_review(Template.id))
						})
				if response.get('addons',{}).get('odoo_marketplace'):
					if Template.marketplace_seller_id and Template.marketplace_seller_id.website_published:
						result.update({
							"offer_terms": self._getOfferTerms(Template,Template.marketplace_seller_id.id),
							'seller_info':{
											'seller_profile_url'	:"/myTemplateseller/%s"%Template.marketplace_seller_id.id,
											'marketplace_seller_id'	:Template.marketplace_seller_id.id,
											'seller_name'			:Template.marketplace_seller_id.name,
											'seller_city'			:Template.marketplace_seller_id.city or "",
											'seller_state'			:Template.marketplace_seller_id.state_id.name or "",
											'seller_country'		:Template.marketplace_seller_id.country_id.name or "",
											'seller_profile_image'	: self.get_marketplace_image_url(self.base_url, 'res.partner', Template.marketplace_seller_id.id,'profile_image'),
											'average_rating'		: Template.marketplace_seller_id.avg_review(),
											'total_reviews'			:len(Template.marketplace_seller_id.seller_review_ids.filtered(lambda r: (r.active == True and r.state == "pub"))),
											'message'				:str(Template.marketplace_seller_id.total_active_recommendation()[1])+" positive feedback (%s ratings)"%Template.marketplace_seller_id.avg_review()
											}
							})
					else:
						result.update({
						    "offer_terms": [],
							'seller_info': {}
							})

				for im in Template.product_image_ids:
					result['images'].append(_get_image_url(self.base_url, 'product.image', im.id,'image'))
				for ali in Template.attribute_line_ids:
					temp = {
						"name":ali.attribute_id.name or "",
						"attributeId":ali.attribute_id.id,
						"type":ali.attribute_id.type,
						"newVariant":ali.attribute_id.create_variant,
						"values":[]
						}
					for v in ali.value_ids:
						temp["values"].append({
							"name":v.name or "",
							"valueId":v.id,
							"htmlCode":v.html_color or "",
							"newVariant":ali.attribute_id.create_variant,
							})
					result['attributes'].append(temp)
				if Template.product_variant_count > 1:
					result.update({
					'priceUnit'		:_displayWithCurrency(local.get('lang_obj'),Template.product_variant_id.lst_price, local.get('currencySymbol'), local.get('currencyPosition')),
					'priceReduce'	:Template.product_variant_id.price < Template.product_variant_id.lst_price and _displayWithCurrency(local.get('lang_obj'),Template.product_variant_id.price, local.get('currencySymbol'), local.get('currencyPosition')) or "",
					'productId'		:Template.product_variant_id.id,
					'productCount'	:Template.product_variant_count,
					'description'	:Template.product_variant_id.description_sale or "",
					'thumbNail'		:_get_image_url(self.base_url, 'product.product', Template.product_variant_id.id,'image'),
					'images'		:[_get_image_url(self.base_url, 'product.product', Template.product_variant_id.id,'image')],
					'variants'	:[]
					})
					for var in Template.product_variant_ids:
						temp = {
							"productId":var.id,
							'images':[_get_image_url(self.base_url, 'product.product', var.id,'image')],
							'priceReduce':var.price < var.lst_price and _displayWithCurrency(local.get('lang_obj'),var.price, local.get('currencySymbol'), local.get('currencyPosition')) or "",
							'priceUnit':_displayWithCurrency(local.get('lang_obj'),var.lst_price, local.get('currencySymbol'), local.get('currencyPosition')),
							"combinations":[],
							"addedToWishlist":var.id in wishlist,
							}
						for avl in var.attribute_value_ids:
							temp["combinations"].append({
									"valueId":avl.id,
									"attributeId":avl.attribute_id and avl.attribute_id.id,
								})
						result['variants'].append(temp)
				else:
					result.update({
					'priceUnit'		:_displayWithCurrency(local.get('lang_obj'),Template.lst_price, local.get('currencySymbol'), local.get('currencyPosition')),
					'priceReduce'	:Template.price < Template.lst_price and _displayWithCurrency(local.get('lang_obj'),Template.price, local.get('currencySymbol'), local.get('currencyPosition')) or "",
					'productId'		:Template.product_variant_id and Template.product_variant_id.id or '',
					'productCount'	:Template.product_variant_count,
					'description'	:Template.description_sale or "",
					'thumbNail'		:_get_image_url(self.base_url, 'product.template', Template.id,'image'),
					'images'		:[_get_image_url(self.base_url, 'product.template', Template.id,'image')],
					"addedToWishlist":Template.product_variant_id.id in wishlist
					})
			else:
				result = {'success':False, 'message':'Template not found !!!'}
			response.update(result)
		return self._response('template', response)

	@route(['/mobikul/mycart','/mobikul/mycart/<int:line_id>'], csrf=False, type='http', auth="none", methods=['POST','PUT','DELETE'])
	def getMyCart(self, line_id=0, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner:
				if request.httprequest.method == "POST":
					last_order = Partner.last_website_so_id
					if last_order:
						local = response.get('local',{})
						result = {
						"name":last_order.name,
						"subtotal":{"title":"Subtotal",
									"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_untaxed, local.get('currencySymbol'), local.get('currencyPosition')),
								},
						"tax":{"title":"Taxes",
									"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_tax, local.get('currencySymbol'), local.get('currencyPosition')),
							},
						"grandtotal":{"title":"Total",
									"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_total, local.get('currencySymbol'), local.get('currencyPosition')),
							},
						"items":[]
						}
						for item in last_order.order_line:
							if response.get('addons', {}).get('website_sale_delivery') and item.is_delivery:
								shippingMethod = {
							      	"tax":[tax.name for tax in item.tax_id],
									"name":item.order_id.carrier_id.name,
									"description":item.order_id.carrier_id.website_description or "",
									"shippingId":item.order_id.carrier_id.id,
									"total": _displayWithCurrency(local.get('lang_obj'), item.price_subtotal,
																  local.get('currencySymbol'), local.get('currencyPosition')),
								}
								result.update({"delivery":shippingMethod})
							else:
								temp = {
								"lineId":item.id,
								"templateId":item.product_id and item.product_id.product_tmpl_id.id or "",
								"name":item.product_id and item.product_id.display_name or item.name,
								"thumbNail":_get_image_url(self.base_url, 'product.product', item.product_id and item.product_id.id or "",'image'),
								"priceReduce":item.price_reduce < item.price_unit and _displayWithCurrency(local.get('lang_obj'),item.price_reduce, local.get('currencySymbol'), local.get('currencyPosition')) or "",
								"priceUnit":_displayWithCurrency(local.get('lang_obj'),item.price_unit, local.get('currencySymbol'), local.get('currencyPosition')),
								"qty":item.product_uom_qty,
								"total":_displayWithCurrency(local.get('lang_obj'),item.price_subtotal, local.get('currencySymbol'), local.get('currencyPosition')),
								# "discount":item.discount and "(%d%% OFF)"%item.discount or "",
								"discount":item.discount and item.discount or "",
								}
								result['items'].append(temp)
						if not len(result['items']):
							result['message'] = _('Your Shopping Bag is empty.')
					else:
						result = {'message':_('Your Shopping Bag is empty.')}
				else:
					OrderLineObj = request.env['sale.order.line'].sudo()
					OrderLine = OrderLineObj.search([('id','=',line_id)])
					if OrderLine:
						if request.httprequest.method == "PUT":
							result = {'message':'Updated successfully.'}
							if self._mData.get('set_qty'):
								OrderLine.product_uom_qty = self._mData.get('set_qty')
							elif self._mData.get('add_qty'):
								OrderLine.product_uom_qty +=int(self._mData['add_qty'])
							else:
								result = {'message':'Wrong request.'}
						elif request.httprequest.method == "DELETE":
							try:
								result = {'message':'%s'%(OrderLine.product_id and OrderLine.product_id.name or OrderLine.name)+_(' was removed from your Shopping Bag.')}
								OrderLine.unlink()
							except:
								result = {'message':'Please try again after some time.'}
						else:
							result = {'message':'Wrong request.'}
					else:
						result = {'message':'No matching product found !!!'}
			else:
				result = {'success':False, 'message':'Account not found !!!'}
			response.update(result)
		return self._response('cart', response)

	@route('/mobikul/mycart/setToEmpty', csrf=False, type='http', auth="none", methods=['DELETE'])
	def setToEmpty(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner:
				last_order = Partner.last_website_so_id
				if last_order:
					try:
						result = {'message':_('Your Shopping Bag has been set to Empty.')}
						last_order.order_line.unlink()
						result['cartCount'] = last_order.cart_count
					except:
						result = {'message':'Please try again after some time.'}
				else:
					result = {'message':_('Your Shopping Bag is already empty.')}
			else:
				result = {'success':False, 'message':'Account not found !!!'}
			response.update(result)
		return self._response('setToEmpty', response)

	@route('/mobikul/mycart/addToCart', csrf=False, type='http', auth="none", methods=['POST'])
	def addToCart(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			Mobikul = request.env['mobikul'].sudo()
			result = Mobikul.add_to_cart(response.get('customerId'),self._mData.get("productId"),self._mData.get("set_qty"),self._mData.get("add_qty"),response)
			response.update(result)
		return self._response('addToCart', response)

	@route('/mobikul/paymentAcquirers', csrf=False, type='http', auth="none", methods=['POST'])
	def getPaymentAcquirer(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			AcquirerObj = request.env['payment.acquirer'].sudo()
			Acquirers = AcquirerObj.search_read([('is_mobikul_available','=',1),('mobikul_reference_code','in',AQUIRER_REF_CODES)], fields=['name','pre_msg','mobikul_reference_code'])
			if Acquirers:
				result = {'acquirers':Acquirers}
				for index,value in enumerate(result['acquirers']):
					result['acquirers'][index]['thumbNail'] = _get_image_url(self.base_url, 'payment.acquirer', result['acquirers'][index]['id'],'image')
					result['acquirers'][index]['description'] = remove_htmltags(result['acquirers'][index].pop('pre_msg')) or ""
					result['acquirers'][index]['code'] = result['acquirers'][index].pop('mobikul_reference_code') or ""
			else:
				result = {'success':False, 'message':'No Active Payment methods found.'}
			response.update(result)
		return self._response('paymentAcquirer', response)

	def _computePayfortSdkSignature(self,values,Acquirer):
		keys = values.keys()
		keys.sort()
		sign = ""
		for k in keys:
			sign = sign + k + "=" +str(values[k])
		sign = Acquirer.request_phrase + sign + Acquirer.request_phrase
		sha256sign = hashlib.sha256(sign.encode()).hexdigest()
		return sha256sign

	def _computePayfortPaymentToken(self,val,Acquirer):
		token_url = PAYFORT_SDK_ENV_URL.get(Acquirer.environment)
		result = requests.post(url= token_url, json=val)
		return  result.json()

	def _computePayfortSdkToken(self,Acquirer,Transaction,order_name):
		if self._mData.get('device_id'):
			val = {
					"service_command":"SDK_TOKEN",
					"access_code":Acquirer.access_code,
					"merchant_identifier":Acquirer.merchant_identifier,
					"language":"en",
					"device_id":self._mData.get('device_id'),
				}
			val['signature'] = self._computePayfortSdkSignature(val,Acquirer)
			sdkToken = self._computePayfortPaymentToken(val,Acquirer)
			return {
					'status':True,
					'paymentReference':_get_next_reference(order_name),
					'code':'PAYFORT',
					'auth':True,
					"sdkToken":sdkToken
					}
		else:
			return {
					'status':False,
					'message': "No Device Id Found !!"
					}

	def _getAquirerCredentials(self, order_name, Acquirer,response):
		if Acquirer.mobikul_reference_code == 'COD':
			return {'status':True,'code':'COD','auth':False}
		elif Acquirer.mobikul_reference_code == 'STRIPE_W':
			Transaction = request.env['payment.transaction'].sudo()
			return {'status':True,'paymentReference':Transaction.get_next_reference(order_name),'code':'STRIPE','auth':True,'secret_key':Acquirer.stripe_checkout_client_secret_key,'publishable_key':Acquirer.stripe_checkout_publishable_key}
		elif Acquirer.mobikul_reference_code == 'PAYFORT':
			Transaction = request.env['payment.transaction'].sudo()
			sdkTokenResponse = self._computePayfortSdkToken(Acquirer,Transaction,order_name)
			return sdkTokenResponse
		elif Acquirer.mobikul_reference_code == 'STRIPE_E':
			Transaction = request.env['payment.transaction'].sudo()
			return {'status':True,'paymentReference':Transaction.get_next_reference(order_name),'code':'STRIPE','auth':True,'secret_key':Acquirer.stripe_secret_key,'publishable_key':Acquirer.stripe_publishable_key}
		elif Acquirer.mobikul_reference_code == '2_CHECKOUT':
			Transaction_refNo = request.env['payment.transaction'].sudo().get_next_reference(order_name)
			paymentUrl = "%sapp/payment/2checkout?reference=%s&acquirer_id=%s"%(self.base_url,order_name,Acquirer.id)
			return {'status':True,'paymentUrl':paymentUrl,'code':'2_CHECKOUT','auth':True,"acquire":Acquirer.name}
		elif Acquirer.mobikul_reference_code == 'PAYFORT_SADAD':
			Transaction_refNo = request.env['payment.transaction'].sudo().get_next_reference(order_name)
			paymentUrl = "%sapp/payment/payfortsadad?reference=%s&acquirer_id=%s&sadad_olp="%(self.base_url,order_name,Acquirer.id)
			return {'status':True,'paymentUrl':paymentUrl,'code':'PAYFORT_SADAD','auth':True,"acquire":Acquirer.name}
		else:
			return {'status':False,'message':_('Payment Mode not Available.')}

	def _getAquirerState(self, Acquirer, status=False):
		if Acquirer.mobikul_reference_code in ['COD']:
			return "pending"
		elif Acquirer.mobikul_reference_code in ['STRIPE_W','STRIPE_E']:
			return STATUS_MAPPING['STRIPE'].get(status,'pending')
		else:
			return "pending"


	def _orderReview(self,user,response,Acquirer):
		last_order = user.partner_id.last_website_so_id
		if last_order and len(last_order.order_line):
			local = response.get('local',{})
			if self._mData.get('shippingAddressId'):
				last_order.partner_shipping_id = int(self._mData.get('shippingAddressId'))

			# add shippigMethod
			if response.get('addons', {}).get('website_sale_delivery') and self._mData.get("shippingId"):
				last_order.sudo()._check_carrier_quotation( force_carrier_id=int(self._mData.get("shippingId")))

			result = {
			"name":last_order.name,
			"billingAddress": last_order.partner_invoice_id._display_address(),
			"shippingAddress": last_order.partner_shipping_id._display_address(),
			"paymentAcquirer": Acquirer.name,
			# "paymentPreMessage": Acquirer.pre_msg,
			"subtotal":{"title":"Subtotal",
						"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_untaxed, local.get('currencySymbol'), local.get('currencyPosition')),
					},
			"tax":{"title":"Taxes",
						"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_tax, local.get('currencySymbol'), local.get('currencyPosition')),
				},
			"grandtotal":{"title":"Total",
						"value":_displayWithCurrency(local.get('lang_obj'),last_order.amount_total, local.get('currencySymbol'), local.get('currencyPosition')),
				},
			"amount":last_order.amount_total,
			"currency":last_order.pricelist_id.currency_id.name or "",
			"items":[],
			}

			for item in last_order.order_line:
				if response.get('addons', {}).get('website_sale_delivery') and item.is_delivery:
					shippingMethod = {
				      	"tax":[tax.name for tax in item.tax_id],
						"name":item.order_id.carrier_id.name,
						"description":item.order_id.carrier_id.website_description or "",
						"shippingId":item.order_id.carrier_id.id,
						"total": _displayWithCurrency(local.get('lang_obj'), item.price_subtotal,
													  local.get('currencySymbol'), local.get('currencyPosition')),
					}
					result.update({"delivery":shippingMethod})
				else:
					temp = {
					"lineId":item.id,
					"templateId":item.product_id and item.product_id.product_tmpl_id.id or "",
					"name":item.product_id and item.product_id.display_name or item.name,
					"thumbNail":_get_image_url(self.base_url, 'product.product', item.product_id and item.product_id.id or "",'image'),
					"priceReduce":item.price_reduce < item.price_unit and _displayWithCurrency(local.get('lang_obj'),item.price_reduce, local.get('currencySymbol'), local.get('currencyPosition')) or "",
					"priceUnit":_displayWithCurrency(local.get('lang_obj'),item.price_unit, local.get('currencySymbol'), local.get('currencyPosition')),
					"qty":item.product_uom_qty,
					"total":_displayWithCurrency(local.get('lang_obj'),item.price_subtotal, local.get('currencySymbol'), local.get('currencyPosition')),
					# "discount":item.discount and "(%d%% OFF)"%item.discount or "",
					"discount":item.discount and item.discount or "",
					"max_bonus":item.product_id.product_tmpl_id.max_percentage_bonus or "",
					}
					result['items'].append(temp)

			result['paymentData'] = self._getAquirerCredentials(last_order.name, Acquirer,response)
			result['paymentData'].update({'customer_email':last_order.partner_id.email})
			last_order.payment_acquirer_id = Acquirer.id
		else:
			result = {'success':False, 'message':_('Add some products in order to proceed.')}
		return result

	@route('/mobikul/orderReviewData', csrf=False, type='http', auth="none", methods=['POST'])
	def getOrderReviewData(self, **kwargs):
		Mobikul = request.env['mobikul'].sudo()
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			Acquirer = request.env['payment.acquirer'].sudo().browse(int(self._mData.get('acquirerId')))
			if Acquirer:
				UserObj = request.env['res.users'].sudo()
				user = UserObj.browse(response.get('userId',0))
				if user:
					if response.get('addons',{}).get('email_verification') and Mobikul.email_verification_defaults().get('restrict_unverified_users'):
						if user.wk_token_verified:
							result = self._orderReview(user,response,Acquirer)
						else:
							result = {'success':False,'message':_("You can't place your order, please verify your account") }
					else:
						result = self._orderReview(user,response,Acquirer)
				else:
					result = {'success':False, 'message':_('Account not found !!!')}
			else:
				result = {'success':False, 'message':_('No Payment methods found with given id.')}
			response.update(result)
		return self._response('orderReviewData', response)


	@route('/mobikul/placeMyOrder', csrf=False, type='http', auth="none", methods=['POST'])
	def placeMyOrder(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		result={}
		if response.get('success'):
			result = self.placeOrder(response.get('customerId'))
		response.update(result)
		return self._response('placeMyOrder', response)

	def _sendPaymentAcknowledge(self,last_order,Partner,customerId,result):
		if last_order.payment_tx_id.state != 'error':
			last_order.with_context(send_email=True).action_confirm()
			Partner.last_website_so_id = False
			self._pushNotification(self._mData.get("fcmToken", ""), condition='orderplaced',
							   customer_id=customerId)
			result.update({
			'url':"/mobikul/my/order/%s"%last_order.id,
			'name':last_order.name,
			'cartCount': 0,
			'success': True,
			'message': 'Your order' + ' %s ' % (last_order.name) + 'has been placed successfully.',
			})
			if last_order.payment_tx_id.state in ['pending','draft']:
				result.update({'txn_msg': remove_htmltags(last_order.payment_acquirer_id.pending_msg)})
			elif last_order.payment_tx_id.state == 'done':
				result.update({'txn_msg':remove_htmltags(last_order.payment_acquirer_id.done_msg)})
			elif last_order.payment_tx_id.state == 'cancel':
				result.update({'txn_msg':remove_htmltags(last_order.payment_acquirer_id.cancel_msg)})
			else:
				result.update({'txn_msg':'No transaction state found..'})

		else:
			result.update({
			'success': False,
			'message': "ERROR",
			'txn_msg':last_order.payment_acquirer_id.error_msg or "ERROR"
			})
		return result

	def placeOrder(self,customerId):
		result = {}
		PartnerObj = request.env['res.partner'].sudo()
		Partner = PartnerObj.browse(customerId)
		if Partner:
			last_order = Partner.last_website_so_id
			if last_order:
				if last_order.payment_acquirer_id:
					if last_order.payment_acquirer_id.name in ["2Checkout","PAYFORT_SADAD"]:
						result1 = {
						'paymentReference':"%s"%last_order.payment_tx_id.reference,
						'paymentStatus':"%s"%last_order.payment_tx_id.state,
						}
						result = self._sendPaymentAcknowledge(last_order,Partner,customerId,result)
						result.update(result1)
					else:
						Transaction = request.env['payment.transaction'].sudo()
						tx_values = {
							'acquirer_id': last_order.payment_acquirer_id.id,
							'type': 'form',
							'amount': last_order.amount_total,
							'currency_id': last_order.pricelist_id.currency_id.id,
							'partner_id': last_order.partner_id.id,
							'partner_country_id': last_order.partner_id.country_id.id,
							'reference': self._mData.get('paymentReference',Transaction.get_next_reference(last_order.name)), #ptptpt
							'sale_order_id': last_order.id,
							'state': self._getAquirerState(last_order.payment_acquirer_id,self._mData.get('paymentStatus')), #ptptpt
							'acquirer_reference': 'MOBIKUL',
						}
						tx = Transaction.create(tx_values)
						# update quotation
						last_order.write({
							'payment_tx_id': tx.id,
						})
						result = self._sendPaymentAcknowledge(last_order,Partner,customerId,{})
				else:
					result = {'success':False, 'message':_('No Payment Method found.')}
			else:
				result = {'success':False, 'message':_('Add some products in order to proceed.')}
		else:
			result = {'success':False, 'message':('Account not found !!!')}
		return result

	@route('/mobikul/saveMyDetails', csrf=False, type='http', auth="none", methods=['POST'])
	def saveMyDetails(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			result = {}
			Userobj = request.env['res.users'].sudo()
			User = Userobj.browse(response.get('userId'))
			if User:
				result['message'] = _("Updated Successfully.")
				if self._mData.get('image'):
					try:
						User.write({'image':self._mData['image']})
						result['customerProfileImage'] = _get_image_url(self.base_url, 'res.partner', User.partner_id.id, 'image')
					except Exception,e:

						result['message'] = _("Please try again later")+" %r"%e
				if self._mData.get('name'):
					User.write({'name':self._mData['name']})
				if self._mData.get('password'):
					User.write({'password':self._mData['password']})
			else:
				result = {'success':False, 'message':_('Account not found !!!')}
			response.update(result)
		return self._response('saveMyDetails', response)

	def _tokenUpdate(self, customer_id=False):
		FcmRegister = request.env['fcm.registered.devices'].sudo()
		already_registered = FcmRegister.search([('device_id','=',self._mData.get("fcmDeviceId"))])
		if already_registered:
			already_registered.write({'token':self._mData.get("fcmToken"),'customer_id':customer_id})
		else:
			FcmRegister.create({
				'token':self._mData.get("fcmToken",""),
				'device_id':self._mData.get("fcmDeviceId",""),
				'description':"%r"%self._mData,
				'customer_id':customer_id,
				})
		return True

	def _pushNotification(self, token, condition='signup', customer_id=False):
		notifications = request.env['mobikul.push.notification.template'].sudo().search([('condition','=',condition)])
		for n in notifications:
			n._send({'to':token},customer_id)
		return True

	@route('/mobikul/registerFcmToken', csrf=False, type='http', auth="none", methods=['POST'])
	def registerFcmToken(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			customer_id = False
			if self._mData.get("customerId") and request.env['res.partner'].sudo().browse(int(self._mData["customerId"])).exists():
				customer_id = int(self._mData["customerId"])
			self._tokenUpdate(customer_id=customer_id)
			response.update({'message':_('Request completed !')})
		return self._response('registerFcmToken', response)


	@route('/mobikul/notificationMessages', csrf=False, type='http', auth="none", methods=['POST'])
	def getNotificationMessages(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			fields = ['id','name','create_date','title','subtitle','body','banner','icon','period','datatype','is_read']
			domain = [('customer_id','=',Partner.id)]
			Message = request.env['mobikul.notification.messages'].sudo()
			notification_message = Message.search_read(domain, limit=self._mData.get('limit',response.get('itemsPerPage',5)), offset=self._mData.get('offset',0),  order="id desc", fields=fields)
			for msg in notification_message:
				msg['name'] = msg['name'] or ""
				msg['title'] = msg['title'] or ""
				msg['subtitle'] = msg['subtitle'] or ""
				msg['body'] = msg['body'] or ""
				msg['icon'] = _get_image_url(self.base_url, 'mobikul.notification.messages', msg['id'] ,'icon')
				msg['banner'] = _get_image_url(self.base_url, 'mobikul.notification.messages', msg['id'] ,'banner')
			result = {'all_notification_messages':notification_message}
			response.update(result)

		return self._response('notificationMessages', response)


	@route('/mobikul/notificationMessage/<int:message_id>', csrf=False, type='http', auth="none",  methods=['POST','PUT','DELETE'])
	def getNotificationMessageDetails(self, message_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			MessageObj = request.env['mobikul.notification.messages'].sudo()
			message = MessageObj.search([('id','=',message_id),('customer_id','=',response.get('customerId'))])
			if message:
				if request.httprequest.method == "POST":
					message.is_read = True
					result = {
					'id':message.id,
					'name':message.name or "",
					'create_date':message.create_date ,
					'title':message.title or "",
					'subtitle':message.subtitle or "",
					'body':message.body or "",
					'icon':_get_image_url(self.base_url, 'mobikul.notification.messages', message.id ,'icon'),
					'banner':_get_image_url(self.base_url, 'mobikul.notification.messages', message.id ,'banner'),
					'period':message.period,
					'is_read':message.is_read,
					'datatype':message.datatype,
					'success':True,
					'message':'Successfull'
					}
				elif request.httprequest.method == "DELETE":
					message.active = False
					result = {'success':True, 'message':_('Deleted Successfully')}
				elif request.httprequest.method == "PUT":
					message.is_read = self._mData.get('is_read',message.is_read)
					result = {'success':True, 'message':_('Updated Successfully')}
			else:
				result = {'success':False, 'message':_('Message not Found')}
			response.update(result)
		return self._response('notificationMessageDetails', response)

	@route('/mobikul/my/wishlists', csrf=False, type='http', auth="none", methods=['POST'])
	def getMyWishlists(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				wishlists=[]
				Partner = request.env['res.partner'].sudo().browse(response.get('customerId'))
				local = response.get('local',{})
				for wishlist in Partner.website_wishlist:
					# here template_id is related to product.product
					if wishlist.template_id:
						product_detail = {
							'id'         :wishlist.id,
							"name"       : wishlist.template_id.display_name,
							"thumbNail"  : _get_image_url(self.base_url, 'product.product', wishlist.template_id.id, 'image'),
							"priceReduce":wishlist.template_id.price < wishlist.template_id.lst_price and _displayWithCurrency(local.get('lang_obj'),wishlist.template_id.price, local.get('currencySymbol'), local.get('currencyPosition')) or "",
							"priceUnit"  :_displayWithCurrency(local.get('lang_obj'),wishlist.template_id.lst_price, local.get('currencySymbol'), local.get('currencyPosition')),
							"productId"  : wishlist.template_id.id,
							"templateId" : wishlist.template_id.product_tmpl_id.id,
							'seller_name':wishlist.template_id.product_tmpl_id.marketplace_seller_id.name,
							# website_sale_offer
							"discount"   : wishlist.template_id.product_tmpl_id.pharmacy_discount,
							"max_bonus"  : wishlist.template_id.product_tmpl_id.max_percentage_bonus,

							}
						wishlists.append(product_detail)
				result = {
				'success':True,
				"wishLists":wishlists,
				'WishlistCount':len(wishlists),
				'message':'SUCCESS'
				}
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('myWishlists', response)

	@route('/my/removeWishlist/<int:wishlist_id>', csrf=False, type='http', auth="none",  methods=['DELETE'])
	def removeWishlist(self, wishlist_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				try:
					wishlist = request.env['website.wishlist'].sudo().search([('id','=',wishlist_id)])
					if wishlist:
						wishlist.unlink()
						result={'success':True,
								'message':_('Item removed')
								}
					else:
						result={
							'success':False,
							'message':_('Not Found')
							}
				except Exception,e:
					result={
							'success':False,
							'message':_('Please try again later'),
							'detail':'Error Details: %r'%e,
						}
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('removeWishlist', response)

	@route('/my/removeFromWishlist/<int:product_id>', csrf=False, type='http', auth="none",  methods=['DELETE'])
	def removeFromWishlist(self, product_id, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				try:
					wishlist = request.env['website.wishlist'].sudo().search([('template_id','=',product_id)])
					#here product_id is the template id
					if wishlist:
						wishlist.unlink()
						result={'success':True,
								'message':_('Item removed')
								}
					else:
						result={
							'success':False,
							'message':_('Not Found'),
							}
				except Exception,e:
					result={
							'success':False,
							'message':_('Please try again later'),
							'detail':'Error Details: %r'%e,
						}
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('removeFromWishlist', response)

	@route('/my/addToWishlist', csrf=False, type='http', auth="none", methods=['POST'])
	def addToWishlist(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				result = self.add2Ws(response.get('customerId'), int(self._mData.get("productId")))
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('addToWishlist', response)

	def add2Ws(self, customer_id, product_id):
		try:
			WishlistObj = request.env['website.wishlist'].sudo()
			wishlist_id = WishlistObj.search([('template_id','=',product_id),('partner_id','=',customer_id)])
			if not wishlist_id:
				vals = {
							'template_id': product_id,
							'partner_id': customer_id,
					}
				WishlistObj.create(vals)
				result = {'success':True,
							'message':_("Item moved to Wishlist")
						}
			else:
				result = {'success':False,
							'message':_("Already Added to Wishlist")
						}

		except Exception,e:
			result = {
				'success':False,
				'message':_('Please try again later'),
				'detail':'Error Details: %r'%e,
				}
		return result

	def _myWishlist(self, customer_id, product_id=False):
		result = []
		wishlists = request.env['website.wishlist'].sudo().search([('partner_id','=',customer_id)])
		for wishlist in wishlists:
			result.append(wishlist.template_id.id)
		return result

	@route('/my/wishlistToCart', csrf=False, type='http', auth="none", methods=['POST'])
	def moveWishlistToCart(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				wishlistObj = request.env['website.wishlist'].sudo()
				wishlist = wishlistObj.search([('id','=',self._mData.get("wishlistId")),('partner_id','=',response.get('customerId'))])
				result = request.env['mobikul'].sudo().add_to_cart(response.get('customerId'),wishlist.template_id.id, False, self._mData.get("add_qty",1), response)
				if result.get("success"):
					wishlist.unlink()
					result = {'success':True, 'message':_('Item moved to Bag')}
				else:
					result = {'success':False, 'message':_('Please try again later')}
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('moveWishlistToCart', response)

	@route('/my/cartToWishlist', csrf=False, type='http', auth="none", methods=['POST'])
	def moveCartToWishlist(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				OrderLine = request.env['sale.order.line'].sudo().search([('id','=',self._mData.get('line_id'))])
				if OrderLine:
					result = self.add2Ws(response.get('customerId'), OrderLine.product_id.id)
					if result.get("success"):
						OrderLine.unlink()
						result = {'success':True, 'message':_('Item moved to Wishlist')}
					else:
						result = {'success':False, 'message':_('Please try again later')}
				else:
					result = {'success':False, 'message':_('Order not found')}
			else:
				result = {'success':False, 'message':_('Wishlist is not Active !!!')}
			response.update(result)
		return self._response('moveCartToWishlist', response)

	# @route('/mobikul/my/reviewList', csrf=False, type='http', auth="none", methods=['GET'])
	# def getReviewList(self, **kwargs):
	# 	response = self._authenticate(True, **kwargs)
	# 	if response.get('success'):
	# 		if response.get('addons',{}).get('review') and response.get('customerId'):
	# 			result = {}
	# 			PartnerObj = request.env['res.partner'].sudo()
	# 			Partner = PartnerObj.browse(response.get('customerId'))
	# 			if Partner:
	# 				reviewList = []
	# 				pass
	# 				# write the logic for get customer review
	# 				result = {'all_ReviewList':reviewList}
	# 			else:
	# 				result = {'success':False, 'message':'Account not found !!!'}
	# 		else:
	# 			response.update({'success':False, 'message':'Review Module not install !!!'})
	# 		response.update(result)
	# 	return self._response('reviewList', response)

	@route('/product/reviews', csrf=False, type='http', auth="none", methods=['POST'])
	def getProductReview(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('review'):
				product_reviews = []
				reviewObj = request.env['user.review'].sudo()
				domain = [('template_id','=',self._mData.get('template_id')),('state','=','pub')]
				fields =['customer','customer_image','email','likes','dislikes','rating','title','msg','create_date']
				product_reviews = reviewObj.search_read(domain, limit=self._mData.get('limit',response.get('itemsPerPage',5)), offset=self._mData.get('offset',0),  order="id desc", fields=fields)
				for review in product_reviews:
					review['customer_image'] =  _get_image_url(self.base_url, 'user.review', review['id'] ,'customer_image')
					review['create_date'] =  request.env['mobikul'].sudo().easy_date(review['create_date'])
				result = {'product_reviews':product_reviews,"reviewCount":len(product_reviews)}

			else:
				result = {'success':False, 'message':_('Review Module not install !!!')}
			response.update(result)
		return self._response('ProductReview', response)

	@route('/my/saveReview', csrf=False, type='http', auth="none", methods=['POST'])
	def addReview(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		Mobikul = request.env['mobikul'].sudo()
		if response.get('success'):
			result = {}
			if response.get('addons',{}).get('review') and response.get('customerId'):
				Partner = request.env['res.partner'].sudo().browse(response.get('customerId'))
				if Partner:
					vals={
						"title":self._mData.get("title"),
						'msg':self._mData.get("detail"),
						"rating": self._mData.get("rate"),
						'partner_id':Partner.id,
						"template_id": self._mData.get("template_id"),
						"customer":Partner.name,
						"email":Partner.email,
						"customer_image":Partner.image
					}
					try:
						request.env['user.review'].sudo().create(vals)
						if Mobikul.review_defaults().get('auto_publish'):
							result = {'success':True, 'message':_('Thanks for your review.')}
						else:
							result = {'success':True, 'message': Mobikul.review_defaults().get('message_when_unpublish')}
					except Exception,e:
							result = {'success':False, 'message':_('Please try again later')}
				else:
					result = {'success':False, 'message':_('Account not found !!!')}
			else:
				response.update({'success':False, 'message':_('Review Module not install !!!')})
			response.update(result)
		return self._response('addReview', response)



	@route('/send/verifyEmail', csrf=False, type='http', auth="none", methods=['POST'])
	def verifyEmail(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		Mobikul = request.env['mobikul'].sudo()
		if response.get('success'):
			result={}
			if response.get('addons',{}).get('email_verification') and Mobikul.email_verification_defaults().get('restrict_unverified_users'):
				UserObj = request.env['res.users'].sudo()
				user = UserObj.search([('id','=',response.get('userId'))])
				if not user.wk_token_verified:
					UserObj.send_verification_email(user.id)
					response['message']=_("Verification email sent successfully.")
					response['success'] = True
				else:
					response['message']=_("Email already verified.")
					response['success'] = False
			else:
				response.update({'success':False, 'message':_('Email Verification Module not install !!!')})
			response.update(result)
		return self._response('verifyEmail', response)


	@route('/review/likeDislike', csrf=False, type='http', auth="none", methods=['POST'])
	def addLikeDislike(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		likeDislikeObj = request.env['review.like.dislike'].sudo()
		# it assume that review module is installed
		if response.get('success') and self._mData.get("review_id"):
			review = request.env['user.review'].sudo().browse(self._mData.get("review_id"))
			if review:
				ld_exist = likeDislikeObj.search([("customer_id","=",response.get('userId')),("review_id","=",review.id)])
				vals={
					"customer_id":response.get('userId'),
					'like':self._mData.get("ishelpful"),
					"dislike": not self._mData.get("ishelpful"),
					'review_id':review.id,
				}
				if ld_exist:
					try:
						ld_exist.write(vals)
						result = {'success':True, 'message':_('Thank you for your feedback.')}
					except Exception,e:
							result = {'success':False, 'message':_('Please try again later')}
				else:
					try:
						likeDislikeObj.sudo().create(vals)
						result = {'success':True, 'message':_('Thank you for your feedback.')}
					except Exception,e:
							result = {'success':False, 'message':_('Please try again later')}
			else:
				result = {'success':False, 'message':_('Review not found !!!')}
			response.update(result)
		else:
			response["message"] = _("You need to login first !")
		return self._response('addLikeDislike', response)


	@route('/my/Template/seller/<int:seller_id>', csrf=False, type='http', auth="none",  methods=['GET'])
	def productSellerInfo(self, seller_id, **kwargs):
		response = self._authenticate(False, **kwargs)
		if self.auth:
			result = request.env['mobikul'].sudo().authenticate(self._lcred, True, self._sLogin, context={'base_url':self.base_url})
			response.update(result)
		if response.get('success'):
			if response.get('addons',{}).get('odoo_marketplace'):
				local = response.get('local',{})
				sellerDetail = self.seller_profile_info(seller_id, local)

				if sellerDetail:
					result = {'SellerInfo':sellerDetail,'success':True,'message':_('Seller Found !!!')}
				else:
					result = {'success':False, 'message':_('Seller Not Found !!!')}
			else:
				result = {'success':False, 'message':_('Marketplace is not Active !!!')}

			if response.get('addons',{}).get('wishlist') and response.get('customerId'):
				wishlists = self._myWishlist(response.get('customerId',0))
				result.update({'wishlists':wishlists})
			response.update(result)
		return self._response('productSellerInfo', response)

	def seller_profile_info(self,seller_id, local):
		MobikulObj = request.env['mobikul'].sudo()
		detail = {}
		sellerDetail = request.env['res.partner'].sudo().search([('id','=',seller_id),('seller','=',True)])
		if sellerDetail:
			detail = {
				"seller_id"				:sellerDetail.id,
				'name'					:sellerDetail.name,
				'email'					:sellerDetail.email,
				'average_rating'		: sellerDetail.avg_review(),
				'total_reviews'			:len(sellerDetail.seller_review_ids.filtered(lambda r: (r.active == True and r.state == "pub"))),
				'sales_count'			:sellerDetail.seller_sales_count(),
				'product_count'			:sellerDetail.seller_products_count(),
				'seller_profile_image'	:self.get_marketplace_image_url(self.base_url, 'res.partner', sellerDetail.id, 'profile_image'),
				'seller_profile_banner'	:self.get_marketplace_image_url(self.base_url, 'res.partner', sellerDetail.id,'profile_banner'),
				'create_date'			:sellerDetail.create_date,
				'state'					:sellerDetail.state_id.name or "",
				'country'				:sellerDetail.country_id.name or "",
				'profile_msg'			:remove_htmltags(sellerDetail.profile_msg or "") ,
				'return_policy'			:remove_htmltags(sellerDetail.return_policy or ""),
				'shipping_policy'		:remove_htmltags(sellerDetail.shipping_policy or ""),

			}
			seller_review = []
			reviews = sellerDetail.fetch_active_review2(sellerDetail.id,0,2)
			seller_review = self.getSellerReviewsDetail(reviews)
			detail.update({'seller_reviews':seller_review})
			context = local or {}
			context.update({"domain":"[('marketplace_seller_id','=',%r)]"%sellerDetail.id,
						"order":"create_date desc, id desc",
						"limit":5})
			sellerProducts = MobikulObj.fetch_products(**context)
			detail.update({'sellerProducts':sellerProducts})
		return detail

	def getSellerReviewsDetail(self,reviewsObj):
		reviewsDetail = []
		for review in reviewsObj:
			reviewsDetail.append({
				"create_date":review.create_date,
				"rating":review.rating,
				"not_helpful":review.not_helpful,
				"total_votes":review.total_votes,
				"display_name":review.display_name,
				"message_is_follower":review.message_is_follower,
				"title":review.title,
				"id":review.id,
				"msg":review.msg,
				"helpful":review.helpful,
				"email":review.email,
				"name":review.partner_id.name,
				"image":_get_image_url(self.base_url, 'res.partner', review.partner_id.id,'profile_image'),
			})
		return reviewsDetail


	# view all the product of sellers api "http://192.168.1.86:8010/mobikul/search"
	# {"domain": "[('marketplace_seller_id','=',117)]", "offset": 0, "limit":100}

	@route('/mobikul/marketplace', csrf=False, type='http', auth="none",  methods=['GET'])
	def marketplace(self, **kwargs):
		PartnerObj = request.env['res.partner'].sudo()
		if request.httprequest.headers.get("Login"):
			response = self._authenticate(True, **kwargs)
		else:
			response = self._authenticate(False, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('odoo_marketplace'):
				lst = []
				local = response.get('local',{})
				result = {
					"banner":request.env['website'].sudo().get_mp_config_settings_values().get('landing_page_banner') or self.base_url+"odoo_marketplace/static/src/img/Hero-Banner.png",
					"heading":_("Still Selling Offline? Start Selling Online."),
				}
				sellersObj	= PartnerObj.search([('seller','=',True),('state','=','approved'),('website_published','=',True)],limit=5)
				for seller in sellersObj:
					sellerDetail = self.seller_profile_info(seller.id, local)
					lst.append(sellerDetail)
				result.update({'SellersDetail':lst,'success':True,'message':_('Marketplace page !!!')})
			else:
				result = {'success':False, 'message':_('Marketplace is not Active !!!')}
			response.update(result)
		return self._response('marketplace', response)

	def get_marketplace_image_url(self,base_url, model_name, record_id, field_name, width=0, height=0):
		""" Returns a local url that points to the image field of a given browse record only for marketplace """
		#format of marketplace image url "base_url+/ marketplace / image / 139 / res.partner / profile_banner"
		if base_url and not base_url.endswith("/"):
			base_url = base_url + "/"
		if width or height:
			return '%swebsite/image/%s/%s/%s/%sx%s' % (base_url,model_name,record_id, field_name, width, height)
		else:
			return '%swebsite/image/%s/%s/%s' % (base_url,model_name,record_id, field_name)


	def checkReviewEligibility(self,seller_id,customer_id):
		"""
		this method is responsible for marketplace ['/seller/review/check'] controller
		"""

		sol_objs = request.env["sale.order.line"].sudo().search([("product_id.marketplace_seller_id", "=", seller_id), ("order_id.partner_id", "=", customer_id), ("order_id.state", "in", ["sale", "done"])])
		for_seller_total_review_obj = request.env["seller.review"].sudo().search([('marketplace_seller_id', '=', seller_id), ('partner_id', '=', customer_id)])

		# This code must be used in create of review
		if len(sol_objs.ids) == 0:
			result =  {"success":False,"message" : _("You have to purchase a product of this seller first.")}
		elif len(for_seller_total_review_obj.ids) >= len(sol_objs.ids):
			result = {"success":False,"message" : _("According to your purchase your review limit is over.")}
		else:
			result = {"success":True,"message" : _("Eligible for write a review")}
		return result

	@route('/my/review/seller/<int:seller_id>', csrf=False, type='http', auth="none",  methods=['GET','POST'])
	def reviewSeller(self, seller_id, **kwargs):
		SellReviewObj = request.env['seller.review'].sudo()
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('odoo_marketplace'):
				seller = request.env['res.partner'].sudo().search([('id','=',seller_id),('seller','=',True)])
				if seller:
					if request.httprequest.method == "GET":
						sellerReviewDetail = SellReviewObj.search([('marketplace_seller_id','=',seller_id),('active','=',True),('state','=','pub')])
						reviewsDetail = self.getSellerReviewsDetail(sellerReviewDetail)
						result = {
							'SellerReview':reviewsDetail,
							"seller_image":self.get_marketplace_image_url(self.base_url, 'res.partner',seller.id,'image'),
							'seller_profile_image': self.get_marketplace_image_url(self.base_url, 'res.partner',seller.id,'profile_image'),
							'sellerReviewCount':len(sellerReviewDetail),
							'success':True,
							'message':_('Seller Found !!!')
						}
					elif request.httprequest.method == "POST":
						result = self.checkReviewEligibility(seller_id,response.get('customerId'))
						if result.get('success'):
							if self._mData.get('msg') and self._mData.get('rating') and self._mData.get('title'):
								review = {
									'msg' : self._mData.get('msg'),
									'rating' : int(self._mData.get('rating')),
									'title' : self._mData.get('title'),
									'marketplace_seller_id' : seller_id,
									"partner_id" : response.get('customerId'),
									}
								review_obj = request.env['seller.review'].sudo().create(review)
								result = {'success':True,'message':_('Review create successfully for seller id')+'%s'%seller_id}
							else:
								result = {'success':False,'message':_('Pass the params properly for create review!!!')}
					else:
						result = {'success':False,'message':_('Wrong Request')}
				else:
					result = {'success':False,'message':_('Seller not Found.')}

			else:
				result = {'success':False, 'message':'Marketplace is not Active !!!'}
			response.update(result)
		return self._response('reviewSeller', response)


	@route(['/mobikul/marketplace/seller/review/vote/<int:review_id>'], csrf=False, type='http', auth="none",  methods=['POST'])
	def sellerReviewVote(self, review_id, **kwargs):
		review_help_obj = request.env['review.help'].sudo()
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			if response.get('addons',{}).get('odoo_marketplace'):
				ishelpful = self._mData.get('ishelpful') and "yes" or "no"
				vote_exist = review_help_obj.search( [('seller_review_id', '=', review_id), ('customer_id', '=', response.get('customerId'))])

				if vote_exist:
					vote_exist[0].write({"review_help": ishelpful})
					result = {'success':True, 'message':_('seller review vote update successfully !!!')}
				else:
					review_help_obj.sudo().create({"customer_id": response.get('customerId'), "seller_review_id": review_id, "review_help": ishelpful})
					result = {'success':True, 'message':_('seller review vote create successfully !!!')}
			else:
				result = {'success':False, 'message':_('Marketplace is not Active !!!')}
			response.update(result)
		return self._response('sellerReviewVote', response)

	@route(['/mobikul/marketplace/seller/orderlines'], csrf=False, type='http', auth="none",methods=['POST'])
	def sellerOrderLines(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner and Partner.seller:
				if response.get('addons', {}).get('odoo_marketplace'):
					result = {'success': True}
					local = response.get('local', {})
					SaleOrderLine = request.env['sale.order.line'].sudo()
					domain = [('marketplace_seller_id', '=',response.get('customerId'))]
					if self._mData.get('state'):
						domain += [('marketplace_state','=',self._mData.get('state'))]
					result['tcount'] = SaleOrderLine.search_count(domain)
					orderline = SaleOrderLine.search(domain, limit=self._mData.get('limit', response.get('itemsPerPage', 5)),
												   offset=self._mData.get('offset', 0), order="id desc")
					result['sellerOrderLines'] = []
					for order in orderline:
						temp = {
							'line_id': order.id,
							'create_date': order.create_date,
							'order_reference':order.order_id.id,
							'customer':order.order_partner_id.name,
							'product':order.product_id.name,
							'price_unit':_displayWithCurrency(local.get('lang_obj'), order.price_unit,
														local.get('currencySymbol', ""), local.get('currencyPosition', "")),
							'quantity': order.product_uom_qty,
							'sub_total':_displayWithCurrency(local.get('lang_obj'), order.price_subtotal,
														local.get('currencySymbol', ""), local.get('currencyPosition', "")),
							'delivered_qty':order.qty_delivered,
							'order_state':order.state,
							'marketplace_state':order.marketplace_state,
							'description': order.name,
						}
						result['sellerOrderLines'].append(temp)
				else:
					result = {'success': False, 'message': ('Marketplace is not Active !!!')}
			else:
				result = {'success': False, 'message': ('Customer is not a seller !!!')}
			response.update(result)
		return self._response('sellerOrderLines', response)

	@route(['/mobikul/marketplace/seller/orderline/<int:line_id>'], csrf=False, type='http', auth="none", methods=['GET'])
	def sellerOrderLinesDetail(self,line_id,**kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner and Partner.seller:
				if response.get('addons', {}).get('odoo_marketplace'):
					result = {'success': True}
					local = response.get('local', {})
					SaleOrderLine = request.env['sale.order.line'].sudo()
					domain = [('id', '=', line_id)]
					orderline = SaleOrderLine.search(domain)
					result['sellerOrderLineDetail'] = {
						'line_id': orderline.id,
						'create_date': orderline.create_date,
						'order_reference': orderline.order_id.id,
						'customer': orderline.order_partner_id.name,
						'product': orderline.product_id.name,
						'price_unit': _displayWithCurrency(local.get('lang_obj'), orderline.price_unit,
														   local.get('currencySymbol', ""),
														   local.get('currencyPosition', "")),
						'quantity': orderline.product_uom_qty,
						'sub_total': _displayWithCurrency(local.get('lang_obj'), orderline.price_subtotal,
														  local.get('currencySymbol', ""),
														  local.get('currencyPosition', "")),
						'delivered_qty': orderline.qty_delivered,
						'order_state': orderline.state,
						'marketplace_state': orderline.marketplace_state,
						'description': orderline.name,
						'order_payment_acquirer':orderline.order_payment_acquirer_id.id and orderline.order_payment_acquirer_id.id or "",
						'delivery_method':orderline.order_id.carrier_id and orderline.order_id.carrier_id.id or ""

					}

				else:
					result = {'success': False, 'message': 'Marketplace is not Active !!!'}
			else:
				result = {'success': False, 'message': 'Customer is not a seller !!!'}
			response.update(result)
		return self._response('sellerOrderLinesDetail', response)

	# {"domain": "[('marketplace_seller_id','=',65),('status','=','approved')]", "offset": 0, "limit": 100}

	@route(['/mobikul/marketplace/seller/ask'], csrf=False, type='http', auth="none",methods=['POST'])
	def sellerAsk(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner and Partner.seller:
				ask_Query = "<p><b>%s</b></p><p>%s</p>"%(self._mData.get('title') or "",self._mData.get('body'))
				mail_id = Partner.message_post(body=ask_Query, message_type='comment',subtype="mail.mt_comment",author_id=response.get('customerId'))
				if mail_id:
					result = {'message':_('Seller query is posted Successfully'),'success':True}
				else:
					result = {'message': 'Something went wrong in posted query', 'success': False}
			else:
				result = {'success': False, 'message': 'Customer is not a seller !!!'}
			response.update(result)
		return self._response('sellerAsk', response)

	@route(['/mobikul/marketplace/seller/product'], csrf=False, type='http', auth="none", methods=['POST'])
	def sellerProduct(self, **kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			TemplateObj = request.env['product.template'].sudo()
			if Partner and Partner.seller:
				local = response.get('local', {})
				domain = [('marketplace_seller_id', '=', response.get('customerId'))]
				if self._mData.get('state'):
					domain += [('status', '=', self._mData.get('state'))]
				productDetailsCount = TemplateObj.search_count(domain)
				productDetails = TemplateObj.search(domain,limit=self._mData.get('limit',response.get('itemsPerPage',5)), offset=self._mData.get('offset',0),  order="id desc")
				slr_product = []
				for prd in productDetails:
					temp = {
						"name": prd.name,
						'templateId':prd.id,
						'state': prd.status,
						'thumbNail' :_get_image_url(self.base_url, 'product.template', prd.id,'image'),
						'seller': prd.marketplace_seller_id.name,
						'qty': prd.qty,
						'priceUnit': _displayWithCurrency(local.get('lang_obj'),prd.list_price, local.get('currencySymbol',""), local.get('currencyPosition',"")),
					}
					slr_product.append(temp)
				result= {'success': True,'sellerProduct':slr_product,"tcount":productDetailsCount,"offset":self._mData.get('offset',0)}
			else:
				result = {'success': False, 'message': 'Customer is not a seller !!!'}
			response.update(result)
		return self._response('sellerProduct', response)


	@route(['/mobikul/marketplace/seller/dashboard'], csrf=False, type='http', auth="none",methods=['GET'])
	def sellerDashboard(self, **kwargs):
		Mobikul = request.env["mobikul"].sudo()
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			if Partner and Partner.seller:
				sellerData = Mobikul.sellerDashboardData(seller_Obj=Partner)
				result = {'success': True,'sellerDashboard': sellerData}
			else:
				result = {'success': False, 'message': 'Customer is not a seller !!!'}
			response.update(result)
		return self._response('sellerDashboard', response)

	@route(['/mobikul/marketplace/seller/terms'], csrf=False, type='http', auth="none", methods=['GET'])
	def sellerTermCond(self, **kwargs):
		response = self._authenticate(False, **kwargs)
		if response.get('success'):
			result = {
				"term_and_condition":request.env['website'].sudo().get_mp_config_settings_values().get('term_and_condition',False),
			}
			response.update(result)
		return self._response('sellerTermCond', response)


	@route(['/mobikul/marketplace/become/seller'], csrf=False, type='http', auth="none", methods=['POST'])
	def becomeSeller(self, **kwargs):
		Mobikul = request.env["mobikul"].sudo()
		UserObj = request.env['res.users'].sudo()
		response = self._authenticate(True, **kwargs)
		if response.get('success'):
			user = UserObj.browse([response.get('userId')])
			if not user.partner_id.seller:
				if self._mData.get('url_handler') and Mobikul.checkSellerUniqueUrl(self._mData.get('url_handler')):
					Mobikul.set_marketplace_group_user(user)
					user.partner_id.seller = True
					user.partner_id.url_handler = self._mData.get('url_handler')
					user.partner_id.country_id = self._mData.get('country_id')
					result ={'success': True, 'message': 'Successfully became a seller'}
				else:
					result ={'success': False, 'message': _("Seller profile 'url_handler' is not unique or absent.")}
			else:
				result ={'success': False, 'message': 'Customer is already a seller' }
		response.update(result)
		return self._response('becomeSeller', response)


	@route(['/mobikul/ShippingMethods'], csrf=False, type='http', auth="none", methods=['GET'])
	def getAvailableShippingMethods(self,**kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success') and response.get('addons', {}).get('website_sale_delivery'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			SaleOrder = Partner.last_website_so_id
			ShippingMethods = SaleOrder.sudo()._get_delivery_methods()
			if ShippingMethods:
				local = response.get('local', {})
				result = []
				for method in ShippingMethods:
					result.append({
						"name": method.name,
						"id": method.id,
						"description": method.website_description or "",
						"price":_displayWithCurrency(local.get('lang_obj'), method.with_context({"order_id":SaleOrder.id}).price,
											 local.get('currencySymbol', ""), local.get('currencyPosition', "")),
					})
				result = {'ShippingMethods':result}
			else:
				result = {'success':False, 'message':'No Active Shipping methods found.'}
		else:
			result = {'success':False, 'message':'Website Sale Delivery is not install.'}
		response.update(result)
		return self._response('getAvailableShippingMethods', response)

	@route(['/mobikul/create/pharmacy/account'], csrf=False, type='http', auth="none", methods=['POST'])
	def mobikulPharmacyAccount(self,**kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success') and response.get('addons', {}).get('marketplace_pharmacist_details'):
			PartnerObj = request.env['res.partner'].sudo()
			Partner = PartnerObj.browse(response.get('customerId'))
			orderId = self._mData.get('orderId')
			order = request.env['sale.order'].sudo().browse(int(orderId))
			marketplace_seller_id = order.marketplace_seller_id.id if order.marketplace_seller_id else 0
			pharmacy_request_exist = request.env['pharmacist.id.details'].sudo().search([
                ('pharmacist_customer_id','=', Partner.id),
                ('marketplace_seller_id','=',marketplace_seller_id)]
            )

			if not pharmacy_request_exist:
				name = self._mData.get("name")
				pharmacy_name = self._mData.get("pharmacy_name")
				email = self._mData.get("email")
				phone = self._mData.get("phone")
				street = self._mData.get("street")
				city = self._mData.get("city")
				zip = self._mData.get("zip")
				country_id = self._mData.get("country_id")
				state_id = self._mData.get("state_id")
				comm_registration_file = self._mData.get("comm_reg")
				comm_registration_filename = self._mData.get("comm_reg_filename")
				tax_card = self._mData.get("tax_card")
				tax_card_filename = self._mData.get("tax_card_filename")
				customer_id = Partner
				values = {
				            'name': name,
				            'pharmacist_name': pharmacy_name,
				            'marketplace_seller_id': marketplace_seller_id,
				            'pharmacist_customer_id': customer_id.id,
				            'email': email,
				            'phone': phone,
				            'street1':street,
				            'city':city,
				            'zipcode':zip,
				            'country_id':int(country_id) if country_id else None,
				            'state_id':int(state_id) if state_id else None,
							'comm_registration_file': comm_registration_file,
	                		'comm_registration_filename': comm_registration_filename,
							'tax_card': tax_card,
	                		'tax_card_filename': tax_card_filename,
				        }
				try:
					pharmacy_account_id = request.env["pharmacist.id.details"].sudo().create(values)
					if pharmacy_account_id and order and marketplace_seller_id:
						pharmacy_partner_parent_id = Partner.parent_id if Partner.parent_id else False
						if not pharmacy_partner_parent_id:
							vals = {
						                'name' : pharmacy_name,
						                'email' : email,
						                'is_company' : True,
						            }
							pharmacy_partner_parent_id = request.env['res.partner'].sudo().create(vals)
							Partner.parent_id = pharmacy_partner_parent_id.id

						pharmacy_partner_parent_id.phone = phone if phone else ''
						pharmacy_partner_parent_id.street = street if street else False
						pharmacy_partner_parent_id.city = city if city else ''
						pharmacy_partner_parent_id.zip = zip if zip else ''
						pharmacy_partner_parent_id.country_id = int(country_id) if country_id else False
						pharmacy_partner_parent_id.state_id = int(state_id) if state_id else False
						result = {'success':True, 'message':'Pharmacy registration request create successfully.'}
				except Exception as e:
					result = {'success':False, 'message':'An Exception occur: %r'%e}
			else:
				result = {'success':True, 'message':'A request for this seller is already pending. You cannot create a new request.'}
		else:
			result = {'success':False, 'message':'Marketplace Pharmacist Details is not install.'}
		response.update(result)
		return self._response('mobikulPharmacyAccount', response)

	@route(['/mobikul/pharmacy/account/search'], csrf=False, type='http', auth="none", methods=['POST'])
	def mobikulPharmacyAccountSearch(self,**kwargs):
		response = self._authenticate(True, **kwargs)
		if response.get('success') and response.get('addons', {}).get('marketplace_pharmacist_details'):
			pharmacy_account_id = request.env['pharmacist.id.details'].sudo().search([
		        ('pharmacist_customer_id','=',response.get('customerId')),
		        ('pharmacist_id','=',self._mData.get("pharmacy_account_id")),
		    	], limit=1)
			if pharmacy_account_id:
				result = {'success':True, 'message':'Pharmacy Id Found.'}
			else:
				result = {'success':False, 'message':'Pharmacy Id Not Found'}
		else:
			result = {'success':False, 'message':'Marketplace Pharmacist Details is not install.'}
		response.update(result)
		return self._response('mobikulPharmacyAccountSearch', response)