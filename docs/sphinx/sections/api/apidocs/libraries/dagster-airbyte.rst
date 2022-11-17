Airbyte (dagster-airbyte)
---------------------------

This library provides a Dagster integration with `Airbyte <https://www.airbyte.com/>`_.

For more information on getting started, see the `Airbyte integration guide </integrations/airbyte>`_.

.. currentmodule:: dagster_airbyte

Ops
===

.. autoconfigurable:: airbyte_sync_op


Resources
=========

.. autoconfigurable:: airbyte_resource
    :annotation: ResourceDefinition

.. autoclass:: AirbyteResource
    :members:

Assets
======

.. autofunction:: load_assets_from_airbyte_instance

.. autofunction:: load_assets_from_airbyte_project

.. autofunction:: build_airbyte_assets


Managed Config
==============

.. autoclass:: AirbyteManagedElementReconciler
   :members:
   :special-members: __init__
    
.. autofunction:: load_assets_from_connections


.. autoclass:: AirbyteSource 
   :members:
   :special-members: __init__
.. autoclass:: AirbyteDestination
   :members: 
   :special-members: __init__
.. autoclass:: AirbyteConnection
   :members: 
   :special-members: __init__
.. autoclass:: AirbyteSyncMode
   :members: 


Managed Config Generated Sources
================================

.. currentmodule:: dagster_airbyte.managed.generated.sources

.. autoclass:: StravaSource
    :members:
    :special-members: __init__

.. autoclass:: AppsflyerSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleWorkspaceAdminReportsSource
    :members:
    :special-members: __init__

.. autoclass:: CartSource
    :members:
    :special-members: __init__

.. autoclass:: LinkedinAdsSource
    :members:
    :special-members: __init__

.. autoclass:: MongodbSource
    :members:
    :special-members: __init__

.. autoclass:: TimelySource
    :members:
    :special-members: __init__

.. autoclass:: StockTickerApiTutorialSource
    :members:
    :special-members: __init__

.. autoclass:: WrikeSource
    :members:
    :special-members: __init__

.. autoclass:: CommercetoolsSource
    :members:
    :special-members: __init__

.. autoclass:: GutendexSource
    :members:
    :special-members: __init__

.. autoclass:: IterableSource
    :members:
    :special-members: __init__

.. autoclass:: QuickbooksSingerSource
    :members:
    :special-members: __init__

.. autoclass:: BigcommerceSource
    :members:
    :special-members: __init__

.. autoclass:: ShopifySource
    :members:
    :special-members: __init__

.. autoclass:: AppstoreSingerSource
    :members:
    :special-members: __init__

.. autoclass:: GreenhouseSource
    :members:
    :special-members: __init__

.. autoclass:: ZoomSingerSource
    :members:
    :special-members: __init__

.. autoclass:: TiktokMarketingSource
    :members:
    :special-members: __init__

.. autoclass:: ZendeskChatSource
    :members:
    :special-members: __init__

.. autoclass:: AwsCloudtrailSource
    :members:
    :special-members: __init__

.. autoclass:: OktaSource
    :members:
    :special-members: __init__

.. autoclass:: InsightlySource
    :members:
    :special-members: __init__

.. autoclass:: LinkedinPagesSource
    :members:
    :special-members: __init__

.. autoclass:: PersistiqSource
    :members:
    :special-members: __init__

.. autoclass:: FreshcallerSource
    :members:
    :special-members: __init__

.. autoclass:: AppfollowSource
    :members:
    :special-members: __init__

.. autoclass:: FacebookPagesSource
    :members:
    :special-members: __init__

.. autoclass:: JiraSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleSheetsSource
    :members:
    :special-members: __init__

.. autoclass:: DockerhubSource
    :members:
    :special-members: __init__

.. autoclass:: UsCensusSource
    :members:
    :special-members: __init__

.. autoclass:: KustomerSingerSource
    :members:
    :special-members: __init__

.. autoclass:: AzureTableSource
    :members:
    :special-members: __init__

.. autoclass:: ScaffoldJavaJdbcSource
    :members:
    :special-members: __init__

.. autoclass:: TidbSource
    :members:
    :special-members: __init__

.. autoclass:: QualarooSource
    :members:
    :special-members: __init__

.. autoclass:: YahooFinancePriceSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleAnalyticsV4Source
    :members:
    :special-members: __init__

.. autoclass:: JdbcSource
    :members:
    :special-members: __init__

.. autoclass:: FakerSource
    :members:
    :special-members: __init__

.. autoclass:: TplcentralSource
    :members:
    :special-members: __init__

.. autoclass:: ClickhouseSource
    :members:
    :special-members: __init__

.. autoclass:: FreshserviceSource
    :members:
    :special-members: __init__

.. autoclass:: ZenloopSource
    :members:
    :special-members: __init__

.. autoclass:: OracleSource
    :members:
    :special-members: __init__

.. autoclass:: KlaviyoSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleDirectorySource
    :members:
    :special-members: __init__

.. autoclass:: InstagramSource
    :members:
    :special-members: __init__

.. autoclass:: ShortioSource
    :members:
    :special-members: __init__

.. autoclass:: SquareSource
    :members:
    :special-members: __init__

.. autoclass:: DelightedSource
    :members:
    :special-members: __init__

.. autoclass:: AmazonSqsSource
    :members:
    :special-members: __init__

.. autoclass:: YoutubeAnalyticsSource
    :members:
    :special-members: __init__

.. autoclass:: ScaffoldSourcePythonSource
    :members:
    :special-members: __init__

.. autoclass:: LookerSource
    :members:
    :special-members: __init__

.. autoclass:: GitlabSource
    :members:
    :special-members: __init__

.. autoclass:: ExchangeRatesSource
    :members:
    :special-members: __init__

.. autoclass:: AmazonAdsSource
    :members:
    :special-members: __init__

.. autoclass:: MixpanelSource
    :members:
    :special-members: __init__

.. autoclass:: OrbitSource
    :members:
    :special-members: __init__

.. autoclass:: AmazonSellerPartnerSource
    :members:
    :special-members: __init__

.. autoclass:: CourierSource
    :members:
    :special-members: __init__

.. autoclass:: CloseComSource
    :members:
    :special-members: __init__

.. autoclass:: BingAdsSource
    :members:
    :special-members: __init__

.. autoclass:: PrimetricSource
    :members:
    :special-members: __init__

.. autoclass:: PivotalTrackerSource
    :members:
    :special-members: __init__

.. autoclass:: ElasticsearchSource
    :members:
    :special-members: __init__

.. autoclass:: BigquerySource
    :members:
    :special-members: __init__

.. autoclass:: WoocommerceSource
    :members:
    :special-members: __init__

.. autoclass:: SearchMetricsSource
    :members:
    :special-members: __init__

.. autoclass:: TypeformSource
    :members:
    :special-members: __init__

.. autoclass:: WebflowSource
    :members:
    :special-members: __init__

.. autoclass:: FireboltSource
    :members:
    :special-members: __init__

.. autoclass:: FaunaSource
    :members:
    :special-members: __init__

.. autoclass:: IntercomSource
    :members:
    :special-members: __init__

.. autoclass:: FreshsalesSource
    :members:
    :special-members: __init__

.. autoclass:: AdjustSource
    :members:
    :special-members: __init__

.. autoclass:: BambooHrSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleAdsSource
    :members:
    :special-members: __init__

.. autoclass:: HellobatonSource
    :members:
    :special-members: __init__

.. autoclass:: SendgridSource
    :members:
    :special-members: __init__

.. autoclass:: MondaySource
    :members:
    :special-members: __init__

.. autoclass:: DixaSource
    :members:
    :special-members: __init__

.. autoclass:: SalesforceSource
    :members:
    :special-members: __init__

.. autoclass:: PipedriveSource
    :members:
    :special-members: __init__

.. autoclass:: FileSource
    :members:
    :special-members: __init__

.. autoclass:: GlassfrogSource
    :members:
    :special-members: __init__

.. autoclass:: ChartmogulSource
    :members:
    :special-members: __init__

.. autoclass:: OrbSource
    :members:
    :special-members: __init__

.. autoclass:: CockroachdbSource
    :members:
    :special-members: __init__

.. autoclass:: ConfluenceSource
    :members:
    :special-members: __init__

.. autoclass:: PlaidSource
    :members:
    :special-members: __init__

.. autoclass:: SnapchatMarketingSource
    :members:
    :special-members: __init__

.. autoclass:: MicrosoftTeamsSource
    :members:
    :special-members: __init__

.. autoclass:: LeverHiringSource
    :members:
    :special-members: __init__

.. autoclass:: TwilioSource
    :members:
    :special-members: __init__

.. autoclass:: StripeSource
    :members:
    :special-members: __init__

.. autoclass:: Db2Source
    :members:
    :special-members: __init__

.. autoclass:: SlackSource
    :members:
    :special-members: __init__

.. autoclass:: RechargeSource
    :members:
    :special-members: __init__

.. autoclass:: OpenweatherSource
    :members:
    :special-members: __init__

.. autoclass:: RetentlySource
    :members:
    :special-members: __init__

.. autoclass:: ScaffoldSourceHttpSource
    :members:
    :special-members: __init__

.. autoclass:: YandexMetricaSource
    :members:
    :special-members: __init__

.. autoclass:: TalkdeskExploreSource
    :members:
    :special-members: __init__

.. autoclass:: ChargifySource
    :members:
    :special-members: __init__

.. autoclass:: RkiCovidSource
    :members:
    :special-members: __init__

.. autoclass:: PostgresSource
    :members:
    :special-members: __init__

.. autoclass:: TrelloSource
    :members:
    :special-members: __init__

.. autoclass:: PrestashopSource
    :members:
    :special-members: __init__

.. autoclass:: PaystackSource
    :members:
    :special-members: __init__

.. autoclass:: S3Source
    :members:
    :special-members: __init__

.. autoclass:: SnowflakeSource
    :members:
    :special-members: __init__

.. autoclass:: AmplitudeSource
    :members:
    :special-members: __init__

.. autoclass:: PosthogSource
    :members:
    :special-members: __init__

.. autoclass:: PaypalTransactionSource
    :members:
    :special-members: __init__

.. autoclass:: MssqlSource
    :members:
    :special-members: __init__

.. autoclass:: ZohoCrmSource
    :members:
    :special-members: __init__

.. autoclass:: RedshiftSource
    :members:
    :special-members: __init__

.. autoclass:: AsanaSource
    :members:
    :special-members: __init__

.. autoclass:: SmartsheetsSource
    :members:
    :special-members: __init__

.. autoclass:: MailchimpSource
    :members:
    :special-members: __init__

.. autoclass:: SentrySource
    :members:
    :special-members: __init__

.. autoclass:: MailgunSource
    :members:
    :special-members: __init__

.. autoclass:: OnesignalSource
    :members:
    :special-members: __init__

.. autoclass:: PythonHttpTutorialSource
    :members:
    :special-members: __init__

.. autoclass:: AirtableSource
    :members:
    :special-members: __init__

.. autoclass:: MongodbV2Source
    :members:
    :special-members: __init__

.. autoclass:: FileSecureSource
    :members:
    :special-members: __init__

.. autoclass:: ZendeskSupportSource
    :members:
    :special-members: __init__

.. autoclass:: TempoSource
    :members:
    :special-members: __init__

.. autoclass:: BraintreeSource
    :members:
    :special-members: __init__

.. autoclass:: SalesloftSource
    :members:
    :special-members: __init__

.. autoclass:: LinnworksSource
    :members:
    :special-members: __init__

.. autoclass:: ChargebeeSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleAnalyticsDataApiSource
    :members:
    :special-members: __init__

.. autoclass:: OutreachSource
    :members:
    :special-members: __init__

.. autoclass:: LemlistSource
    :members:
    :special-members: __init__

.. autoclass:: ApifyDatasetSource
    :members:
    :special-members: __init__

.. autoclass:: RecurlySource
    :members:
    :special-members: __init__

.. autoclass:: ZendeskTalkSource
    :members:
    :special-members: __init__

.. autoclass:: SftpSource
    :members:
    :special-members: __init__

.. autoclass:: WhiskyHunterSource
    :members:
    :special-members: __init__

.. autoclass:: FreshdeskSource
    :members:
    :special-members: __init__

.. autoclass:: GocardlessSource
    :members:
    :special-members: __init__

.. autoclass:: ZuoraSource
    :members:
    :special-members: __init__

.. autoclass:: MarketoSource
    :members:
    :special-members: __init__

.. autoclass:: DriftSource
    :members:
    :special-members: __init__

.. autoclass:: PokeapiSource
    :members:
    :special-members: __init__

.. autoclass:: NetsuiteSource
    :members:
    :special-members: __init__

.. autoclass:: HubplannerSource
    :members:
    :special-members: __init__

.. autoclass:: Dv360Source
    :members:
    :special-members: __init__

.. autoclass:: NotionSource
    :members:
    :special-members: __init__

.. autoclass:: ZendeskSunshineSource
    :members:
    :special-members: __init__

.. autoclass:: PinterestSource
    :members:
    :special-members: __init__

.. autoclass:: MetabaseSource
    :members:
    :special-members: __init__

.. autoclass:: HubspotSource
    :members:
    :special-members: __init__

.. autoclass:: HarvestSource
    :members:
    :special-members: __init__

.. autoclass:: GithubSource
    :members:
    :special-members: __init__

.. autoclass:: E2eTestSource
    :members:
    :special-members: __init__

.. autoclass:: MysqlSource
    :members:
    :special-members: __init__

.. autoclass:: MyHoursSource
    :members:
    :special-members: __init__

.. autoclass:: KyribaSource
    :members:
    :special-members: __init__

.. autoclass:: GoogleSearchConsoleSource
    :members:
    :special-members: __init__

.. autoclass:: FacebookMarketingSource
    :members:
    :special-members: __init__

.. autoclass:: SurveymonkeySource
    :members:
    :special-members: __init__

.. autoclass:: PardotSource
    :members:
    :special-members: __init__

.. autoclass:: FlexportSource
    :members:
    :special-members: __init__

.. autoclass:: ZenefitsSource
    :members:
    :special-members: __init__

.. autoclass:: KafkaSource
    :members:
    :special-members: __init__


Managed Config Generated Destinations
=====================================

.. currentmodule:: dagster_airbyte.managed.generated.destinations


.. autoclass:: DynamodbDestination
    :members:
    :special-members: __init__

.. autoclass:: BigqueryDestination
    :members:
    :special-members: __init__

.. autoclass:: RabbitmqDestination
    :members:
    :special-members: __init__

.. autoclass:: KvdbDestination
    :members:
    :special-members: __init__

.. autoclass:: ClickhouseDestination
    :members:
    :special-members: __init__

.. autoclass:: AmazonSqsDestination
    :members:
    :special-members: __init__

.. autoclass:: MariadbColumnstoreDestination
    :members:
    :special-members: __init__

.. autoclass:: KinesisDestination
    :members:
    :special-members: __init__

.. autoclass:: AzureBlobStorageDestination
    :members:
    :special-members: __init__

.. autoclass:: KafkaDestination
    :members:
    :special-members: __init__

.. autoclass:: ElasticsearchDestination
    :members:
    :special-members: __init__

.. autoclass:: MysqlDestination
    :members:
    :special-members: __init__

.. autoclass:: SftpJsonDestination
    :members:
    :special-members: __init__

.. autoclass:: GcsDestination
    :members:
    :special-members: __init__

.. autoclass:: CassandraDestination
    :members:
    :special-members: __init__

.. autoclass:: FireboltDestination
    :members:
    :special-members: __init__

.. autoclass:: GoogleSheetsDestination
    :members:
    :special-members: __init__

.. autoclass:: DatabricksDestination
    :members:
    :special-members: __init__

.. autoclass:: BigqueryDenormalizedDestination
    :members:
    :special-members: __init__

.. autoclass:: SqliteDestination
    :members:
    :special-members: __init__

.. autoclass:: MongodbDestination
    :members:
    :special-members: __init__

.. autoclass:: RocksetDestination
    :members:
    :special-members: __init__

.. autoclass:: OracleDestination
    :members:
    :special-members: __init__

.. autoclass:: CsvDestination
    :members:
    :special-members: __init__

.. autoclass:: S3Destination
    :members:
    :special-members: __init__

.. autoclass:: AwsDatalakeDestination
    :members:
    :special-members: __init__

.. autoclass:: MssqlDestination
    :members:
    :special-members: __init__

.. autoclass:: PubsubDestination
    :members:
    :special-members: __init__

.. autoclass:: R2Destination
    :members:
    :special-members: __init__

.. autoclass:: JdbcDestination
    :members:
    :special-members: __init__

.. autoclass:: KeenDestination
    :members:
    :special-members: __init__

.. autoclass:: TidbDestination
    :members:
    :special-members: __init__

.. autoclass:: FirestoreDestination
    :members:
    :special-members: __init__

.. autoclass:: ScyllaDestination
    :members:
    :special-members: __init__

.. autoclass:: RedisDestination
    :members:
    :special-members: __init__

.. autoclass:: MqttDestination
    :members:
    :special-members: __init__

.. autoclass:: RedshiftDestination
    :members:
    :special-members: __init__

.. autoclass:: PulsarDestination
    :members:
    :special-members: __init__

.. autoclass:: SnowflakeDestination
    :members:
    :special-members: __init__

.. autoclass:: PostgresDestination
    :members:
    :special-members: __init__

.. autoclass:: ScaffoldDestinationPythonDestination
    :members:
    :special-members: __init__

.. autoclass:: LocalJsonDestination
    :members:
    :special-members: __init__

.. autoclass:: MeilisearchDestination
    :members:
    :special-members: __init__