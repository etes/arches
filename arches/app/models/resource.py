'''
ARCHES - a program developed to inventory and manage immovable cultural heritage.
Copyright (C) 2013 J. Paul Getty Trust and World Monuments Fund

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
'''

import importlib
from django.conf import settings
from arches.app.models import models
from arches.app.search.search_engine_factory import SearchEngineFactory
from elasticsearch import Elasticsearch
from arches.app.search.elasticsearch_dsl_builder import Query, Terms
from arches.app.views.concept import get_preflabel_from_valueid
from arches.app.utils.betterJSONSerializer import JSONSerializer, JSONDeserializer
from arches.app.datatypes import datatypes

class Resource(models.ResourceInstance):

    class Meta:
        proxy = True

    def __init__(self, *args, **kwargs):
        super(Resource, self).__init__(*args, **kwargs)
        # from models.ResourceInstance
        # self.resourceinstanceid
        # self.graph
        # self.resourceinstancesecurity
        # end from models.ResourceInstance
        self.tiles = []

    @property
    def primaryname(self):
        module = importlib.import_module('arches.app.functions.resource_functions')
        PrimaryNameFunction = getattr(module, 'PrimaryNameFunction')()
        functionConfig = models.FunctionXGraph.objects.filter(graph=self.graph, function__functiontype='primaryname')
        if len(functionConfig) == 1:
            return PrimaryNameFunction.get(self, functionConfig[0].config)
        else:
            return 'undefined'

    @staticmethod
    def bulk_save(resources):
        pass
    
    def index(self):
        """
        Indexes all the nessesary items values of a resource to support search

        """

        document, terms = self.get_documents_to_index()
        se = SearchEngineFactory().create()
        se.index_data('resource', self.graph_id, JSONSerializer().serializeToPython(document), id=self.pk)

        for term in terms:
            se.index_term(term['term'], term['term_id'], term['context'], term['options'])

    def get_documents_to_index(self, fetchTiles=True):
        """
        Gets all the documents nessesary to index a single resource
        returns a tuple of a document and list of terms

        Keyword Arguments:
        tiles -- pass in a list of tiles instead of fetching them from the database

        """

        document = JSONSerializer().serializeToPython(self)
        document['tiles'] = models.TileModel.objects.filter(resourceinstance=self) if fetchTiles else self.tiles
        document['strings'] = []
        document['dates'] = []
        document['domains'] = []
        document['geometries'] = []
        document['numbers'] = []

        terms = []

        for tile in document['tiles']:
            for nodeid, nodevalue in tile.data.iteritems():
                #node = models.Node.objects.get(pk=nodeid)
                datatype = models.Node.objects.values_list('datatype', flat=True).get(pk=nodeid)
                #print datatype
                if nodevalue != '' and nodevalue != [] and nodevalue != {} and nodevalue is not None:
                    datatype_instance = datatypes.get_datatype_instance(datatype)
                    datatype_instance.append_to_document(document, nodevalue)
                    if datatype == 'string' and (settings.WORDS_PER_SEARCH_TERM == None or (len(nodevalue.split(' ')) < settings.WORDS_PER_SEARCH_TERM)):
                        terms.append({'term': nodevalue, 'term_id': '%s_%s' % (str(tile.tileid), str(nodeid)), 'context': '', 'options': {}})

        return document, terms

    @staticmethod
    def bulk_index(resources):
        print 'in bulk_index'
        se = SearchEngineFactory().create()
        documents = []
        term_list = []
        for resource in resources:
            document, terms = resource.get_documents_to_index(fetchTiles=False)
            documents.append(se.create_bulk_item(index='resource', type=document['graph_id'], id=document['resourceinstanceid'], data=document))
            for term in terms:
                se.index_term(term['term'], term['term_id'], term['context'], term['options'])
                #term_list.append(se.create_bulk_item(index='term', type='value', id=term['term_id'], data=term))

        se.bulk_index(documents)
        #se.bulk_index(term_list)

    def serialize(self):
        """
        serialize to a different form then used by the internal class structure

        used to append additional values (like parent ontology properties) that
        internal objects (like models.Nodes) don't support

        """

        ret = JSONSerializer().handle_model(self)
        ret['tiles'] = self.tiles

        return JSONSerializer().serializeToPython(ret)

    def delete(self):
        es = Elasticsearch()
        se = SearchEngineFactory().create()
        related_resources = self.get_related_resources(lang="en-US", start=0, limit=15)
        for rr in related_resources['resource_relationships']:
            models.ResourceXResource.objects.get(pk=rr['resourcexid']).delete()
        se.delete(index='resource', doc_type=str(self.graph_id), id=self.resourceinstanceid)
        super(Resource, self).delete()

    def get_related_resources(self, lang='en-US', limit=1000, start=0):
        ret = {
            'resource_instance': self,
            'resource_relationships': [],
            'related_resources': []
        }
        se = SearchEngineFactory().create()
        query = Query(se, limit=limit, start=start)
        query.add_filter(Terms(field='resourceinstanceidfrom', terms=self.resourceinstanceid).dsl, operator='or')
        query.add_filter(Terms(field='resourceinstanceidto', terms=self.resourceinstanceid).dsl, operator='or')
        resource_relations = query.search(index='resource_relations', doc_type='all')
        ret['total'] = resource_relations['hits']['total']
        instanceids = set()
        for relation in resource_relations['hits']['hits']:
            relation['_source']['preflabel'] = get_preflabel_from_valueid(relation['_source']['relationshiptype'], lang)
            ret['resource_relationships'].append(relation['_source'])
            instanceids.add(relation['_source']['resourceinstanceidto'])
            instanceids.add(relation['_source']['resourceinstanceidfrom'])
        if len(instanceids) > 0:
            instanceids.remove(str(self.resourceinstanceid))

        related_resources = se.search(index='resource', doc_type='_all', id=list(instanceids))
        if related_resources:
            for resource in related_resources['docs']:
                ret['related_resources'].append(resource['_source'])

        return ret
