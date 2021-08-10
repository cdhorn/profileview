# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2001-2007  Donald N. Allingham
# Copyright (C) 2008       Raphael Ackermann
# Copyright (C) 2009-2010  Gary Burton
# Copyright (C) 2010       Benny Malengier
# Copyright (C) 2012       Doug Blank <doug.blank@gmail.com>
# Copyright (C) 2015-2016  Nick Hall
# Copyright (C) 2015       Serge Noiraud
# Copyright (C) 2021       Christopher Horn
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#

"""
Address Profile Page
"""

# -------------------------------------------------------------------------
#
# Gramps Modules
#
# -------------------------------------------------------------------------
from gramps.gen.const import GRAMPS_LOCALE as glocale
from gramps.gen.errors import WindowActiveError
from gramps.gui.uimanager import ActionGroup
from gramps.gui.widgets.reorderfam import Reorder


# -------------------------------------------------------------------------
#
# Plugin Modules
#
# -------------------------------------------------------------------------
from ..frames.frame_address import AddressGrampsFrame
from ..frames.frame_classes import GrampsState, GrampsOptions
from ..frames.frame_const import _LEFT_BUTTON
from ..frames.frame_person import PersonGrampsFrame
from ..frames.frame_utils import button_activated
from ..groups.group_utils import (
    get_citations_group,
    get_notes_group,
    get_urls_group,
)
from .page_base import BaseProfilePage

_ = glocale.translation.sgettext


class AddressProfilePage(BaseProfilePage):
    """
    Provides the address profile page view with information about the
    address of a person.
    """

    def __init__(self, dbstate, uistate, config):
        BaseProfilePage.__init__(self, dbstate, uistate, config)
        self.order_action = None
        self.family_action = None
        self.reorder_sensitive = None
        self.child = None
        self.colors = None
        self.active_profile = None

    def obj_type(self):
        return "Person"

    def page_type(self):
        return "Address"

    def define_actions(self, view):
        self.order_action = ActionGroup(name="ChangeOrder")
        self.order_action.add_actions([("ChangeOrder", self.reorder)])

        self.family_action = ActionGroup(name="Family")
        self.family_action.add_actions(
            [
                ("AddSpouse", self.add_spouse),
                ("AddParents", self.add_parents),
                ("ShareFamily", self.select_parents),
            ]
        )

        view._add_action_group(self.order_action)
        view._add_action_group(self.family_action)

    def enable_actions(self, uimanager, person):
        uimanager.set_actions_visible(self.family_action, True)
        uimanager.set_actions_visible(self.order_action, True)

    def disable_actions(self, uimanager):
        uimanager.set_actions_visible(self.family_action, False)
        uimanager.set_actions_visible(self.order_action, False)

    def render_page(self, header, vbox, person, secondary=None):
        list(map(header.remove, header.get_children()))
        list(map(vbox.remove, vbox.get_children()))
        if not person:
            return

        grstate = GrampsState(
            self.dbstate, self.uistate, self.callback_router, self.config, self.page_type().lower()
        )
        groptions = GrampsOptions("options.active.person")
        self.active_profile = PersonGrampsFrame(grstate, groptions, person)

        address = secondary
        groptions = GrampsOptions("options.active.address")
        frame = AddressGrampsFrame(grstate, groptions, person, address)

        groups = self.config.get("options.page.address.layout.groups").split(",")
        obj_groups = {}

        if "citation" in groups:
            obj_groups.update({"citation": get_citations_group(grstate, address)})
        if "url" in groups:
            obj_groups.update({"url": get_urls_group(grstate, address)})
        if "note" in groups:
            obj_groups.update({"note": get_notes_group(grstate, address)})
        body = self.render_group_view(obj_groups)

        if self.config.get("options.global.pin-header"):
            header.pack_start(self.active_profile, False, False, 0)
            header.pack_start(frame, False, False, 0)
            header.show_all()
        else:
            vbox.pack_start(self.active_profile, False, False, 0)
            vbox.pack_start(frame, False, False, 0)
        self.child = body
        vbox.pack_start(self.child, True, True, 0)
        vbox.show_all()

        family_handle_list = person.get_parent_family_handle_list()
        self.reorder_sensitive = len(family_handle_list) > 1
        family_handle_list = person.get_family_handle_list()
        if not self.reorder_sensitive:
            self.reorder_sensitive = len(family_handle_list) > 1
        return True

    def reorder_button_press(self, obj, event, handle):
        if button_activated(event, _LEFT_BUTTON):
            self.reorder(obj)

    def reorder(self, *obj):
        if self.active_profile:
            try:
                Reorder(
                    self.dbstate,
                    self.uistate,
                    [],
                    self.active_profile.obj.get_handle(),
                )
            except WindowActiveError:
                pass

    def add_spouse(self, *obj):
        if self.active_profile:
            self.active_profile.add_new_spouse()

    def select_parents(self, *obj):
        if self.active_profile:
            self.active_profile.add_existing_parents()

    def add_parents(self, *obj):
        if self.active_profile:
            self.active_profile.add_new_parents()