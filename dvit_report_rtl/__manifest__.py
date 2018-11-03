# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2015 Mohamed M. Hagag.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


{
    'name': 'Report RTL',
    'version': '10.0.2.0',
    'author': 'DVIT',
    'sequence': 1,
    'summary': 'Report printing in RTL direction',
    'description':
        """
Report printing in RTL direction
==================================
**Note: if you have website installed install Report RTL website**
        """,
    'depends': ['report'],
    'auto_install': False,
    'installable': True,
    'data': [
        'views/layout.xml',
    ],
}
