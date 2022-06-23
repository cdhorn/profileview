#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2007-2009  Douglas S. Blank <doug.blank@gmail.com>
# Copyright (C) 2010       Jakim Friant
# Copyright (C) 2022       Christopher Horn
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
Statistics service worker
"""

# -------------------------------------------------------------------------
#
# Python Modules
#
# -------------------------------------------------------------------------
import io
import os
import sys
import time
import pickle
import argparse
from bisect import bisect
from multiprocessing import Process, Queue

# -------------------------------------------------------------------------
#
# Gramps Modules
#
# -------------------------------------------------------------------------
from gramps.gen.datehandler import get_date
from gramps.gen.db import DBLOCKFN, DBMODE_R
from gramps.gen.db.utils import (
    lookup_family_tree,
    make_database,
    write_lock_file,
)
from gramps.gen.lib import Citation, Person
from gramps.gen.utils.alive import probably_alive
from gramps.gen.utils.file import media_path_full


def examine_people(args, queue):
    """
    Parse and analyze people.
    """

    def get_gender_stats(gender_stats, gender):
        """
        Return gender statistics.
        """
        if gender not in gender_stats:
            gender_stats[gender] = {
                "total": 0,
                "private": 0,
                "tagged": 0,
                "uncited": 0,
                "births_missing": 0,
                "births_missing_date": 0,
                "births_missing_place": 0,
                "births_uncited": 0,
                "births_private": 0,
                "deaths_missing": 0,
                "deaths_missing_date": 0,
                "deaths_missing_place": 0,
                "deaths_uncited": 0,
                "deaths_private": 0,
                "living": 0,
                "living_not_private": 0,
            }
        return gender_stats[gender]

    gender_stats = {}
    media, media_refs = 0, 0
    incomplete_names, no_families = 0, 0
    association_roles = {}
    association, association_refs = 0, 0
    association_private, association_uncited = 0, 0
    participant_roles = {}
    participant, participant_refs = 0, 0
    participant_private = 0
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_people = db.get_number_of_people()

    for person in db.iter_people():
        length = len(person.media_list)
        if length > 0:
            media += 1
            media_refs += length

        for name in [person.get_primary_name()] + person.get_alternate_names():
            if name.get_first_name().strip() == "":
                incomplete_names += 1
            else:
                if name.get_surname_list():
                    for surname in name.get_surname_list():
                        if surname.get_surname().strip() == "":
                            incomplete_names += 1
                else:
                    incomplete_names += 1

        if not person.parent_family_list and not person.family_list:
            no_families += 1

        gender = get_gender_stats(gender_stats, person.get_gender())
        gender["total"] += 1
        if person.private:
            gender["private"] += 1
        if person.tag_list:
            gender["tagged"] += 1
        if not person.citation_list:
            gender["uncited"] += 1

        birth_ref = person.get_birth_ref()
        if birth_ref:
            birth = db.get_event_from_handle(birth_ref.ref)
            if not get_date(birth):
                gender["births_missing_date"] += 1
            if not birth.place:
                gender["births_missing_place"] += 1
            if not birth.citation_list:
                gender["births_uncited"] += 1
            if birth.private:
                gender["births_private"] += 1
        else:
            gender["births_missing"] += 1

        death_ref = person.get_death_ref()
        if death_ref:
            death = db.get_event_from_handle(death_ref.ref)
            if not get_date(death):
                gender["deaths_missing_date"] += 1
            if not death.place:
                gender["deaths_missing_place"] += 1
            if not death.citation_list:
                gender["deaths_uncited"] += 1
            if death.private:
                gender["deaths_private"] += 1
        else:
            gender["deaths_missing"] += 1
            if probably_alive(person, db):
                gender["living"] += 1
                if not person.private:
                    gender["living_not_private"] += 1

        if person.person_ref_list:
            association += 1
            for person_ref in person.person_ref_list:
                association_refs += 1
                if person_ref.private:
                    association_private += 1
                if not person_ref.citation_list:
                    association_uncited += 1
                if person_ref.rel not in association_roles:
                    association_roles[person_ref.rel] = 0
                association_roles[person_ref.rel] += 1

        if person.event_ref_list:
            participant += 1
            for event_ref in person.event_ref_list:
                participant_refs += 1
                role = event_ref.get_role().serialize()
                if role not in participant_roles:
                    participant_roles[role] = 0
                participant_roles[role] += 1
                if event_ref.private:
                    participant_private += 1
        analyze_change(last_changed, person.handle, person.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Person": last_changed},
        "person": {
            "person_total": total_people,
            "person_incomplete_names": incomplete_names,
            "person_no_family_connection": no_families,
        },
        "media": {
            "person_media": media,
            "person_media_refs": media_refs,
        },
        "uncited": {},
        "privacy": {},
        "tag": {},
    }
    for gender in gender_stats:
        if gender == Person.MALE:
            prefix = "male_"
        elif gender == Person.FEMALE:
            prefix = "female_"
        elif gender == Person.UNKNOWN:
            prefix = "unknown_"
        else:
            prefix = "%s_" % str(gender)
        for (key, value) in gender_stats[gender].items():
            new_key = "%s%s" % (prefix, key)
            if "uncited" in new_key:
                index = "uncited"
            elif "private" in new_key:
                index = "privacy"
            elif "tagged" in new_key:
                index = "tag"
            else:
                index = "person"
            payload[index].update({new_key: value})
    append = {
        "association": {
            "association_total": association,
            "association_refs": association_refs,
            "association_roles": association_roles,
        },
        "participant": {
            "participant_total": participant,
            "participant_refs": participant_refs,
            "participant_roles": participant_roles,
        },
        "uncited": {
            "association_uncited": association_uncited,
        },
        "privacy": {
            "association_private": association_private,
            "participant_private": participant_private,
        },
    }
    fold(payload, append)
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "People", total_people, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_families(args, queue):
    """
    Parse and analyze families.
    """
    media, media_refs = 0, 0
    missing_one, missing_both = 0, 0
    family_relations = {}
    uncited, no_events, private, tagged = 0, 0, 0, 0
    child, no_child, child_private, child_uncited = 0, 0, 0, 0
    child_mother_relations, child_father_relations = {}, {}
    participant_roles = {}
    participant_private = 0
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_families = db.get_number_of_families()
    total_surnames = len(set(db.surname_list))

    for family in db.iter_families():
        length = len(family.media_list)
        if length > 0:
            media += 1
            media_refs += length

        if not family.father_handle and not family.mother_handle:
            missing_both += 1
        elif not family.father_handle or not family.mother_handle:
            missing_one += 1

        family_type = family.type.serialize()
        if family_type not in family_relations:
            family_relations[family_type] = 0
        family_relations[family_type] += 1

        if not family.citation_list:
            uncited += 1
        if family.private:
            private += 1
        if family.tag_list:
            tagged += 1

        if not family.event_ref_list:
            no_events += 1
        else:
            for event_ref in family.event_ref_list:
                role = event_ref.get_role().serialize()
                if role not in participant_roles:
                    participant_roles[role] = 0
                participant_roles[role] += 1
                if event_ref.private:
                    participant_private += 1

        if not family.child_ref_list:
            no_child += 1
        else:
            for child_ref in family.child_ref_list:
                child += 1
                if child_ref.private:
                    child_private += 1
                if not child_ref.citation_list:
                    child_uncited += 1
                mother_relation = child_ref.mrel.serialize()
                if mother_relation not in child_mother_relations:
                    child_mother_relations[mother_relation] = 0
                child_mother_relations[mother_relation] += 1
                father_relation = child_ref.frel.serialize()
                if father_relation not in child_father_relations:
                    child_father_relations[father_relation] = 0
                child_father_relations[father_relation] += 1
        analyze_change(last_changed, family.handle, family.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Family": last_changed},
        "family": {
            "family_total": total_families,
            "family_surname_total": total_surnames,
            "family_missing_one": missing_one,
            "family_missing_both": missing_both,
            "family_no_child": no_child,
            "family_relations": family_relations,
            "family_no_events": no_events,
        },
        "uncited": {
            "family_uncited": uncited,
            "child_uncited": child_uncited,
        },
        "privacy": {
            "family_private": private,
            "child_private": child_private,
            "family_participant_private": participant_private,
        },
        "tag": {
            "family_tagged": tagged,
        },
        "children": {
            "child_refs": child,
            "child_mother_relations": child_mother_relations,
            "child_father_relations": child_father_relations,
        },
        "participant": {
            "family_participant_roles": participant_roles,
        },
        "media": {
            "family_media": media,
            "family_media_refs": media_refs,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Families", total_families, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_events(args, queue):
    """
    Parse and analyze events.
    """
    media, media_refs = 0, 0
    no_date, no_place, no_description = 0, 0, 0
    uncited, private, tagged = 0, 0, 0
    event_types = {}
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_events = db.get_number_of_events()

    for event in db.iter_events():
        length = len(event.media_list)
        if length > 0:
            media += 1
            media_refs += length

        if not event.citation_list:
            uncited += 1
        if not event.place:
            no_place += 1
        if not get_date(event):
            no_date += 1
        if not event.get_description():
            no_description += 1
        if event.private:
            private += 1
        if event.tag_list:
            tagged += 1

        event_type = event.get_type().serialize()
        if event_type not in event_types:
            event_types[event_type] = 0
        event_types[event_type] += 1
        analyze_change(last_changed, event.handle, event.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Event": last_changed},
        "event": {
            "event_total": total_events,
            "event_no_place": no_place,
            "event_no_date": no_date,
            "event_no_description": no_description,
            "event_types": event_types,
        },
        "uncited": {
            "event_uncited": uncited,
        },
        "privacy": {
            "event_private": private,
        },
        "tag": {
            "event_tagged": tagged,
        },
        "media": {
            "event_media": media,
            "event_media_refs": media_refs,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Events", total_events, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_places(args, queue):
    """
    Parse and analyze places.
    """
    media, media_refs = 0, 0
    no_name, no_latitude, no_longitude, no_code = 0, 0, 0, 0
    uncited, private, tagged = 0, 0, 0
    place_types = {}
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_places = db.get_number_of_places()

    for place in db.iter_places():
        length = len(place.media_list)
        if length > 0:
            media += 1
            media_refs += length

        place_type = place.get_type().serialize()
        if place_type not in place_types:
            place_types[place_type] = 0
        place_types[place_type] += 1

        if not place.name:
            no_name += 1
        if not place.lat:
            no_latitude += 1
        if not place.long:
            no_longitude += 1
        if not place.code:
            no_code += 1
        if not place.citation_list:
            uncited += 1
        if place.private:
            private += 1
        if place.tag_list:
            tagged += 1
        analyze_change(last_changed, place.handle, place.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Place": last_changed},
        "place": {
            "place_total": total_places,
            "place_no_name": no_name,
            "place_no_latitude": no_latitude,
            "place_no_longitude": no_longitude,
            "place_no_code": no_code,
            "place_types": place_types,
        },
        "uncited": {
            "place_uncited": uncited,
        },
        "privacy": {
            "place_private": private,
        },
        "tag": {
            "place_tagged": tagged,
        },
        "media": {
            "place_media": media,
            "place_media_refs": media_refs,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Places", total_places, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_media(args, queue):
    """
    Parse and analyze media objects.
    """
    no_desc, no_date, no_path, no_mime = 0, 0, 0, 0
    uncited, private, tagged, size_bytes = 0, 0, 0, 0
    not_found = []
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_media = db.get_number_of_media()

    for media in db.iter_media():
        if not media.desc:
            no_desc += 1
        if not get_date(media):
            no_date += 1
        if not media.mime:
            no_mime += 1
        if media.private:
            private += 1
        if media.tag_list:
            tagged += 1
        if not media.path:
            no_path += 1
        else:
            fullname = media_path_full(db, media.path)
            try:
                size_bytes += os.path.getsize(fullname)
            except OSError:
                if media.path not in not_found:
                    not_found.append(media.path)
        analyze_change(last_changed, media.handle, media.change, 20)

    megabytes = int(size_bytes / 1048576)
    payload = {
        "changed": {"Media": last_changed},
        "media": {
            "media_total": total_media,
            "media_size": megabytes,
            "media_no_path": no_path,
            "media_not_found": not_found,
            "media_no_description": no_desc,
            "media_no_date": no_date,
            "media_no_mime": no_mime,
        },
        "uncited": {
            "media_uncited": uncited,
        },
        "privacy": {
            "media_private": private,
        },
        "tag": {
            "media_tagged": tagged,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Media", total_media, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_sources(args, queue):
    """
    Parse and analyze sources.
    """
    media, media_refs = 0, 0
    no_title, no_author, no_pubinfo, no_abbrev = 0, 0, 0, 0
    no_repository, private, tagged = 0, 0, 0
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_sources = db.get_number_of_sources()

    for source in db.iter_sources():
        length = len(source.media_list)
        if length > 0:
            media += 1
            media_refs += length

        if not source.title:
            no_title += 1
        if not source.author:
            no_author += 1
        if not source.pubinfo:
            no_pubinfo += 1
        if not source.abbrev:
            no_abbrev += 1
        if not source.reporef_list:
            no_repository += 1
        if source.private:
            private += 1
        if source.tag_list:
            tagged += 1
        analyze_change(last_changed, source.handle, source.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Source": last_changed},
        "source": {
            "source_total": total_sources,
            "source_no_title": no_title,
            "source_no_author": no_author,
            "source_no_pubinfo": no_pubinfo,
            "source_no_abbrev": no_abbrev,
            "source_no_repository": no_repository,
        },
        "privacy": {
            "source_private": private,
        },
        "tag": {
            "source_tagged": tagged,
        },
        "media": {
            "source_media": media,
            "source_media_refs": media_refs,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Sources", total_sources, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_citations(args, queue):
    """
    Parse and analyze citation objects.
    """
    media, media_refs = 0, 0
    no_source, no_page, no_date, private, tagged = 0, 0, 0, 0, 0
    very_low, low, normal, high, very_high = 0, 0, 0, 0, 0
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_citations = db.get_number_of_citations()

    for citation in db.iter_citations():
        length = len(citation.media_list)
        if length > 0:
            media += 1
            media_refs += length

        if not get_date(citation):
            no_date += 1
        if not citation.source_handle:
            no_source += 1
        if not citation.page:
            no_page += 1
        if citation.private:
            private += 1
        if citation.tag_list:
            tagged += 1
        if citation.confidence == Citation.CONF_VERY_LOW:
            very_low += 1
        elif citation.confidence == Citation.CONF_LOW:
            low += 1
        elif citation.confidence == Citation.CONF_NORMAL:
            normal += 1
        elif citation.confidence == Citation.CONF_HIGH:
            high += 1
        elif citation.confidence == Citation.CONF_VERY_HIGH:
            very_high += 1
        analyze_change(last_changed, citation.handle, citation.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Citation": last_changed},
        "citation": {
            "citation_total": total_citations,
            "citation_no_source": no_source,
            "citation_no_date": no_date,
            "citation_no_page": no_page,
            "citation_very_low": very_low,
            "citation_low": low,
            "citation_normal": normal,
            "citation_high": high,
            "citation_very_high": very_high,
        },
        "privacy": {
            "citation_private": private,
        },
        "tag": {
            "citation_tagged": tagged,
        },
        "media": {
            "citation_media": media,
            "citation_media_refs": media_refs,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Citations", total_citations, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_repositories(args, queue):
    """
    Parse and analyze repositories.
    """
    no_name, no_address, private, tagged = 0, 0, 0, 0
    repository_types = {}
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_repositories = db.get_number_of_repositories()

    for repository in db.iter_repositories():
        repository_type = repository.get_type().serialize()
        if repository_type not in repository_types:
            repository_types[repository_type] = 0
        repository_types[repository_type] += 1

        if not repository.name:
            no_name += 1
        if not repository.address_list:
            no_address += 1
        if repository.private:
            private += 1
        if repository.tag_list:
            tagged += 1
        analyze_change(last_changed, repository.handle, repository.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Repository": last_changed},
        "repository": {
            "repository_total": total_repositories,
            "repository_no_name": no_name,
            "repository_no_address": no_address,
            "repository_types": repository_types,
        },
        "privacy": {
            "repository_private": private,
        },
        "tag": {
            "repository_tagged": tagged,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Repositories",
                total_repositories,
                time.time() - args.start_time,
            ),
            file=sys.stderr,
        )


def examine_notes(args, queue):
    """
    Parse and analyze notes.
    """
    no_text, private, tagged = 0, 0, 0
    note_types = {}
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_notes = db.get_number_of_notes()

    for note in db.iter_notes():
        note_type = note.get_type().serialize()
        if note_type not in note_types:
            note_types[note_type] = 0
        note_types[note_type] += 1

        if not note.text:
            no_text += 1
        if note.private:
            private += 1
        if note.tag_list:
            tagged += 1
        analyze_change(last_changed, note.handle, note.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Note": last_changed},
        "note": {
            "note_total": total_notes,
            "note_no_text": no_text,
            "note_types": note_types,
        },
        "privacy": {
            "note_private": private,
        },
        "tag": {
            "note_tagged": tagged,
        },
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Notes", total_notes, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_tags(args, queue):
    """
    Parse and analyze tags.
    """
    last_changed = []

    db = open_readonly_database(args.tree_name)
    total_tags = db.get_number_of_tags()

    for tag in db.iter_tags():
        analyze_change(last_changed, tag.handle, tag.change, 20)
    close_readonly_database(db)

    payload = {
        "changed": {"Tag": last_changed},
        "tag": {"tag_total": total_tags},
    }
    queue.put(payload)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Tags", total_tags, time.time() - args.start_time
            ),
            file=sys.stderr,
        )


def examine_bookmarks(args):
    """
    Parse and analyze bookmarks.
    """
    db = open_readonly_database(args.tree_name)
    person_bookmarks = len(db.get_bookmarks().bookmarks)
    family_bookmarks = len(db.get_family_bookmarks().bookmarks)
    event_bookmarks = len(db.get_event_bookmarks().bookmarks)
    place_bookmarks = len(db.get_place_bookmarks().bookmarks)
    media_bookmarks = len(db.get_media_bookmarks().bookmarks)
    source_bookmarks = len(db.get_source_bookmarks().bookmarks)
    citation_bookmarks = len(db.get_citation_bookmarks().bookmarks)
    repository_bookmarks = len(db.get_repo_bookmarks().bookmarks)
    note_bookmarks = len(db.get_note_bookmarks().bookmarks)
    total_bookmarks = (
        person_bookmarks
        + family_bookmarks
        + event_bookmarks
        + place_bookmarks
        + media_bookmarks
        + source_bookmarks
        + citation_bookmarks
        + repository_bookmarks
        + note_bookmarks
    )
    payload = {
        "bookmarks": {
            "bookmarks_person": person_bookmarks,
            "bookmarks_family": family_bookmarks,
            "bookmarks_event": event_bookmarks,
            "bookmarks_place": place_bookmarks,
            "bookmarks_media": media_bookmarks,
            "bookmarks_source": source_bookmarks,
            "bookmarks_citation": citation_bookmarks,
            "bookmarks_repository": repository_bookmarks,
            "bookmarks_note": note_bookmarks,
        }
    }
    close_readonly_database(db)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Bookmarks", total_bookmarks, time.time() - args.start_time
            ),
            file=sys.stderr,
        )
    return payload


def analyze_change(obj_list, obj_handle, change, max_length):
    """
    Analyze a change for inclusion in a last modified list.
    """
    bsindex = bisect(KeyWrapper(obj_list, key=lambda c: c[1]), change)
    obj_list.insert(bsindex, (obj_handle, change))
    if len(obj_list) > max_length:
        obj_list.pop(max_length)


# ------------------------------------------------------------------------
#
# KeyWrapper class
#
# ------------------------------------------------------------------------
class KeyWrapper:
    """
    For bisect to operate on an element of a tuple in the list.
    """

    __slots__ = "iter", "key"

    def __init__(self, iterable, key):
        self.iter = iterable
        self.key = key

    def __getitem__(self, i):
        return self.key(self.iter[i])

    def __len__(self):
        return len(self.iter)


def open_readonly_database(dbname):
    """
    Open database for read only access.
    """
    data = lookup_family_tree(dbname)
    dbpath, dummy_locked, dummy_locked_by, backend = data
    database = make_database(backend)
    database.load(dbpath, mode=DBMODE_R, update=False)
    return database


def close_readonly_database(db):
    """
    Close database making sure lock persists for core application as
    existing code will delete it when closing a read only instance.
    """
    save_dir = db.get_save_path()
    if not os.path.isfile(os.path.join(save_dir, DBLOCKFN)):
        save_dir = None
    db.close(update=False)
    if save_dir:
        write_lock_file(save_dir)


def get_object_list(dbname):
    """
    Prepare object list based on descending number of objects.
    """
    db = open_readonly_database(dbname)
    object_list = [
        ("Person", db.get_number_of_people()),
        ("Family", db.get_number_of_families()),
        ("Event", db.get_number_of_events()),
        ("Place", db.get_number_of_places()),
        ("Media", db.get_number_of_media()),
        ("Source", db.get_number_of_sources()),
        ("Citation", db.get_number_of_citations()),
        ("Repository", db.get_number_of_repositories()),
        ("Note", db.get_number_of_notes()),
        ("Tag", db.get_number_of_tags()),
    ]
    close_readonly_database(db)
    object_list.sort(key=lambda x: x[1], reverse=True)
    total = sum([y for (x, y) in object_list])
    return total, [x for (x, y) in object_list]


def fold(one, two):
    """
    Fold a set of dictionary entries into another.
    """
    for key in two.keys():
        if key not in one:
            one.update({key: two[key]})
        else:
            for subkey in two[key]:
                if subkey not in one[key]:
                    one[key].update({subkey: two[key][subkey]})


TASK_HANDLERS = {
    "Person": examine_people,
    "Family": examine_families,
    "Event": examine_events,
    "Place": examine_places,
    "Media": examine_media,
    "Source": examine_sources,
    "Citation": examine_citations,
    "Repository": examine_repositories,
    "Note": examine_notes,
    "Tag": examine_tags,
}


def main():
    """
    Main program.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--tree",
        dest="tree_name",
        required=True,
        help="Tree name",
    )
    parser.add_argument(
        "-T",
        "--time",
        dest="time",
        default=False,
        action="store_true",
        help="Dump run times",
    )
    parser.add_argument(
        "-y",
        "--yaml",
        dest="yaml",
        default=False,
        action="store_true",
        help="Dump statistics in YAML format if YAML support available",
    )
    args = parser.parse_args()

    try:
        total, obj_list = get_object_list(args.tree_name)
    except TypeError:
        print(
            "Error: Problem finding and loading tree: %s" % args.tree_name,
            file=sys.stderr,
        )
        sys.exit(1)

    if args.time:
        args.start_time = time.time()
        print("Run started", file=sys.stderr)

    workers = {}
    queues = {}
    for obj_type in obj_list:
        queues[obj_type] = Queue()
        workers[obj_type] = Process(
            target=TASK_HANDLERS[obj_type], args=(args, queues[obj_type])
        )
        workers[obj_type].start()

    facts = examine_bookmarks(args)
    obj_list.reverse()
    for obj_type in obj_list:
        result_set = queues[obj_type].get()
        workers[obj_type].join()
        fold(facts, result_set)

    if args.yaml:
        try:
            import yaml

            print(yaml.dump(facts))
        except ModuleNotFoundError:
            print("YAML support not available", file=sys.stderr)
    else:
        # https://stackoverflow.com/questions/38029058/sending-pickled-data-to-a-server
        sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding="latin-1")
        print(pickle.dumps(facts).decode("latin-1"), end="", flush=True)
    if args.time:
        print(
            "{0:<12} {1:6} {2}".format(
                "Run complete", total, time.time() - args.start_time
            ),
            file=sys.stderr,
        )
    sys.exit(0)


if __name__ == "__main__":
    main()