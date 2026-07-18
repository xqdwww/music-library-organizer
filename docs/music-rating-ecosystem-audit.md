# Music Rating Ecosystem Audit

Audit ID: `MUSIC_RATING_ECOSYSTEM_AUDIT`

Audit date: 2026-07-18

## Executive decision

The project must not treat MusicBrainz and Discogs as the final source boundary. It should, however, reuse mature components by layer instead of importing every visible score:

1. **Canonical identity:** retain MusicBrainz release-group/release/recording/work relationships; reuse beets, Picard, AcoustID, and Discogs matching where they have stronger established behavior.
2. **Open review evidence:** CritiqueBrainz is the strongest next adapter candidate because it exposes licensed, entity-linked ratings and review text through a documented API.
3. **Classical identity:** MusicBrainz work/performance relationships remain the primary recording identity. Picard Classical Extras, beets ParentWork, Open Opus, and DOREMUS are enrichment layers, not interchangeable recording ratings.
4. **Jazz identity:** match leader, personnel, recording date, session, label, catalogue number, master, release, and edition. MusicBrainz and Discogs provide the practical open base; specialist discographies remain references unless licensed structured access is obtained.
5. **Professional publications:** preserve awards and reviews as independent evidence. Do not normalize them into `music_score` until the recording identity, reuse rights, coverage bias, and conversion rule have been validated.
6. **No public interface:** search snippets, page scraping, popularity, sales, listeners, charts, model inference, and unlicensed copies are excluded from `music_score`.

## Mandatory evidence model

Classical and jazz evidence must be stored before any score conversion:

```json
{
  "publication": "",
  "rating_raw": "",
  "rating_scale": "",
  "award": "",
  "review_type": "",
  "reviewer": "",
  "recording_identity": "",
  "source_reference": ""
}
```

For classical recordings, `recording_identity` must include the work plus conductor, orchestra/ensemble/soloists, recording date or year when known, label, and catalogue/release identifiers. For jazz, it must include leader, personnel/session evidence, studio/live status, recording date, label/catalogue number, and master/release/edition relationship. A title-only match is never sufficient.

## Candidate components

### 1. MusicBrainz Web Service and database dumps

- component_name: MusicBrainz
- repository_or_source: https://musicbrainz.org/doc/MusicBrainz_API and https://github.com/metabrainz/musicbrainz-server
- license: core data CC0; supplementary data CC BY-NC-SA 3.0; server license is project-specific open source
- maintenance_status: ACTIVE
- last_activity: repository pushed 2026-07-17; API documentation current at audit
- community_size_if_available: 1,066 GitHub stars and 335 forks for server; larger editor community not quantified here
- installation_method: hosted WS/2 API; twice-weekly dumps; Docker/local mirror
- language_or_runtime: HTTP JSON/XML; Perl/PostgreSQL server
- supported_entities: artist, release group, release, medium, track, recording, work, label, series, relationships, ratings
- album_matching_capability: STRONG; MBIDs, disc IDs, barcodes, release/release-group hierarchy, recordings and works
- rating_capability: community ratings on supported entities, including release groups
- professional_review_capability: NO
- classical_support: STRONG work hierarchy and performer/conductor/orchestra relationships; completeness varies
- jazz_support: STRONG general relationship model; session completeness varies
- popular_music_support: STRONG
- edition_or_recording_version_support: STRONG release vs release-group vs recording model
- API_or_interface_available: WS/2, search, dumps, replication
- offline_cache_support: full dumps/mirror and client cache
- rate_limit_or_access_constraints: meaningful User-Agent; hosted API rate limits; non-commercial service is free
- test_coverage_if_available: extensive server tests in repository; adapter fixture tests exist locally
- integration_complexity: MEDIUM hosted; HIGH local mirror
- recommended_integration_mode: WRAPPER_INTEGRATION

### 2. beets and built-in metadata plugins

- component_name: beets
- repository_or_source: https://github.com/beetbox/beets and https://beets.readthedocs.io/
- license: MIT
- maintenance_status: ACTIVE
- last_activity: pushed 2026-07-17; v2.11.0 released 2026-05-06
- community_size_if_available: 15,398 stars; 2,063 forks
- installation_method: `pip install beets`; CLI and Python plugin API
- language_or_runtime: Python
- supported_entities: local items/albums plus MusicBrainz, Discogs, AcoustID and plugin metadata
- album_matching_capability: STRONG mature autotagger and distance model
- rating_capability: not a mature cross-source rating aggregator
- professional_review_capability: NO
- classical_support: MEDIUM/HIGH with MusicBrainz fields and ParentWork
- jazz_support: MEDIUM; benefits from MusicBrainz/Discogs but lacks a session-specific model
- popular_music_support: STRONG
- edition_or_recording_version_support: STRONG for release matching; depends on source data
- API_or_interface_available: CLI, Python plugin API, SQLite library
- offline_cache_support: local library; source behavior varies
- rate_limit_or_access_constraints: inherited from metadata sources
- test_coverage_if_available: large maintained test suite and CI
- integration_complexity: MEDIUM; importing it wholesale would overlap current scanner/state responsibilities
- recommended_integration_mode: WRAPPER_INTEGRATION

### 3. MusicBrainz Picard plus Classical Extras and Work & Movement

- component_name: Picard plugin ecosystem
- repository_or_source: https://picard.musicbrainz.org/plugins/ and https://github.com/metabrainz/picard-plugins
- license: Picard GPL-2.0; plugin repository licenses must be checked per plugin
- maintenance_status: ACTIVE
- last_activity: Picard pushed 2026-07-16; plugin repository pushed 2026-05-29
- community_size_if_available: Picard 5,018 stars/458 forks; plugins 176 stars/117 forks
- installation_method: Picard desktop app and plugin install
- language_or_runtime: Python/Qt
- supported_entities: MusicBrainz releases, recordings, works, performers, tags
- album_matching_capability: STRONG interactive matching and AcoustID support
- rating_capability: NO aggregation layer
- professional_review_capability: NO
- classical_support: STRONG work/movement hierarchy and role-aware performers
- jazz_support: MEDIUM via MusicBrainz relationships
- popular_music_support: STRONG
- edition_or_recording_version_support: STRONG when the chosen MusicBrainz release is correct
- API_or_interface_available: plugin API and exported file tags; not a headless rating service
- offline_cache_support: local tags/files; network metadata normally required
- rate_limit_or_access_constraints: inherited MusicBrainz/linked-source limits
- test_coverage_if_available: Picard has maintained tests; per-plugin coverage varies
- integration_complexity: LOW for importing existing MBIDs/tags; HIGH for GUI automation
- recommended_integration_mode: DATA_IMPORT

### 4. CritiqueBrainz

- component_name: CritiqueBrainz
- repository_or_source: https://critiquebrainz.org/ws/1 and https://github.com/metabrainz/critiquebrainz
- license: server GPL-2.0-or-later; reviews carry explicit Creative Commons licenses per record
- maintenance_status: ACTIVE
- last_activity: pushed 2026-06-23; latest listed release 2025-11-17
- community_size_if_available: 73 stars; 60 forks; API example reported 9,197 reviews at documentation capture
- installation_method: hosted JSON API; self-host from repository; optional data import subject to licenses
- language_or_runtime: Python/Flask/PostgreSQL
- supported_entities: release group, recording, work, artist, event, place, label and other Brainz entities
- album_matching_capability: STRONG when a MusicBrainz entity is already resolved
- rating_capability: 1-5 ratings with average and count
- professional_review_capability: YES; licensed review text/source fields, but provenance must be inspected per item
- classical_support: POSSIBLE at release-group/recording/work level; coverage NOT_MEASURED
- jazz_support: POSSIBLE at release-group/recording level; coverage NOT_MEASURED
- popular_music_support: YES; coverage NOT_MEASURED
- edition_or_recording_version_support: entity type distinguishes release group, recording and work; no release entity in documented review types
- API_or_interface_available: documented JSON API
- offline_cache_support: project database/self-hosting and local HTTP cache
- rate_limit_or_access_constraints: hosted-service policy and per-review license; exact production rate limit UNKNOWN
- test_coverage_if_available: repository contains pytest configuration and test command
- integration_complexity: LOW/MEDIUM because MusicBrainz IDs already exist in the canonical layer
- recommended_integration_mode: WRAPPER_INTEGRATION

### 5. Discogs API, dumps, and python3-discogs-client

- component_name: Discogs
- repository_or_source: https://www.discogs.com/developers and https://github.com/joalla/discogs_client
- license: restricted API/content terms; client license requires direct repository verification
- maintenance_status: ACTIVE
- last_activity: client pushed 2026-07-15
- community_size_if_available: client 410 stars; 60 forks
- installation_method: REST API, monthly data dumps, `pip install python3-discogs-client`
- language_or_runtime: HTTP JSON/XML dumps; Python client
- supported_entities: artists, labels, masters, releases, tracks, credits, formats, catalogue numbers
- album_matching_capability: STRONG for physical releases and master/release hierarchy
- rating_capability: explicit community rating/count on release; `want`/`have` are popularity and excluded
- professional_review_capability: NO
- classical_support: MEDIUM; release data can identify labels/catalogue numbers but work/performance semantics are weaker
- jazz_support: HIGH for credits, label, catalogue number, master/reissue relationships; session dates may be incomplete
- popular_music_support: STRONG
- edition_or_recording_version_support: STRONG master/release distinction
- API_or_interface_available: REST API and dumps
- offline_cache_support: monthly dumps and local HTTP cache
- rate_limit_or_access_constraints: API terms, revocable access, rate limits, and restricted data fields
- test_coverage_if_available: client repository tests; local fixture verifies main-release rating behavior
- integration_complexity: MEDIUM
- recommended_integration_mode: WRAPPER_INTEGRATION

### 6. TheAudioDB

- component_name: TheAudioDB
- repository_or_source: https://www.theaudiodb.com/free_music_api
- license: API use under TheAudioDB terms; user-created content described as public-domain/Creative Commons
- maintenance_status: ACTIVE
- last_activity: API/docs current in 2026
- community_size_if_available: site reports 324,887 albums and 3,890,038 tracks at audit
- installation_method: v1/v2 HTTP API; no dependency required
- language_or_runtime: JSON/OpenAPI
- supported_entities: artist, album, track, artwork, MBIDs and cross-database IDs
- album_matching_capability: MEDIUM/HIGH when MusicBrainz release-group ID is present
- rating_capability: `intScore` and `intScoreVotes`; provenance and sparse-vote behavior require validation
- professional_review_capability: `strReview` exists but source/licensing consistency is UNKNOWN
- classical_support: LOW/MEDIUM generic album model
- jazz_support: LOW/MEDIUM generic album model
- popular_music_support: STRONGER coverage
- edition_or_recording_version_support: LIMITED generic album identity
- API_or_interface_available: documented v1/v2 APIs and OpenAPI descriptions
- offline_cache_support: consumer must cache
- rate_limit_or_access_constraints: free 30/min, premium 100/min, business 120/min; v2 premium
- test_coverage_if_available: service internals UNKNOWN
- integration_complexity: LOW technically, MEDIUM/HIGH evidence validation
- recommended_integration_mode: OPTIONAL_DEPENDENCY

### 7. AcoustID and Chromaprint

- component_name: AcoustID/Chromaprint
- repository_or_source: https://acoustid.org/ and https://github.com/acoustid/chromaprint
- license: fingerprint database CC BY-SA 3.0; server MIT; current Chromaprint code MIT
- maintenance_status: ACTIVE
- last_activity: AcoustID server updated 2026-03-15; Chromaprint repository activity in 2026
- community_size_if_available: Chromaprint about 1,270 stars/169 forks; AcoustID server 83 stars
- installation_method: `brew install chromaprint`, `fpcalc`, pyacoustid, hosted API or self-host
- language_or_runtime: C++/Python/HTTP
- supported_entities: audio fingerprints and linked MusicBrainz recordings
- album_matching_capability: INDIRECT; recording identity can constrain an album match
- rating_capability: NO
- professional_review_capability: NO
- classical_support: useful for exact audio recording identity, not work/performance metadata by itself
- jazz_support: useful for exact audio/master detection, not session semantics by itself
- popular_music_support: STRONG exact-audio identity
- edition_or_recording_version_support: detects near-identical recordings; not a release-edition model
- API_or_interface_available: fingerprint CLI/library and web API
- offline_cache_support: local fingerprints/index possible
- rate_limit_or_access_constraints: API key and service limits; existing local key reliability must be tested
- test_coverage_if_available: Chromaprint documents a GoogleTest suite; pyacoustid has tests
- integration_complexity: MEDIUM
- recommended_integration_mode: OPTIONAL_DEPENDENCY

### 8. Open Opus

- component_name: Open Opus
- repository_or_source: https://openopus.org/ and https://github.com/openopus-org/openopus_api
- license: API GPL-3.0; data public domain
- maintenance_status: SLOW/UNCERTAIN
- last_activity: repository pushed 2024-02-03; hosted API still available at audit
- community_size_if_available: 148 stars; 33 forks; about 220 composers and 26,666 dumped work rows
- installation_method: public REST API, full JSON dump, or PHP/MySQL self-host
- language_or_runtime: PHP/MySQL/JSON
- supported_entities: composers, works, genres, performers; explicitly no recordings
- album_matching_capability: NO
- rating_capability: popular/recommended work flags are not album ratings and are excluded
- professional_review_capability: NO
- classical_support: HIGH for simple composer/work taxonomy, limited catalogue detail
- jazz_support: NO
- popular_music_support: NO
- edition_or_recording_version_support: NO
- API_or_interface_available: unauthenticated REST API and full dump
- offline_cache_support: full public-domain JSON dump and self-host cache
- rate_limit_or_access_constraints: no authentication documented; service capacity is community-funded
- test_coverage_if_available: UNKNOWN
- integration_complexity: LOW as reference index; not suitable as recording evaluator
- recommended_integration_mode: LOCAL_INDEX

### 9. DOREMUS knowledge graph

- component_name: DOREMUS
- repository_or_source: https://data.doremus.org/ and https://github.com/DOREMUS-ANR/doremus-ontology
- license: dataset and ontology CC BY 4.0
- maintenance_status: MAINTAINED_DATASET; development activity is modest
- last_activity: ontology pushed 2024-07-09; endpoint available at audit
- community_size_if_available: ontology 24 stars; 10 forks
- installation_method: public SPARQL endpoint, RDF download/local triple store
- language_or_runtime: RDF/OWL/SPARQL
- supported_entities: works, performances, publications, recordings and institutional catalogue links
- album_matching_capability: MEDIUM/HIGH for covered classical catalogue entities; mapping required
- rating_capability: NO general rating layer
- professional_review_capability: NO
- classical_support: STRONG semantic model
- jazz_support: POSSIBLE but not primary focus
- popular_music_support: LIMITED
- edition_or_recording_version_support: STRONG conceptual model for work/performance/recording/publication
- API_or_interface_available: SPARQL and linked data
- offline_cache_support: RDF data can be indexed locally
- rate_limit_or_access_constraints: public endpoint capacity; attribution required
- test_coverage_if_available: ontology validation exists; application-level coverage UNKNOWN
- integration_complexity: HIGH due RDF mapping and coverage reconciliation
- recommended_integration_mode: LOCAL_INDEX

### 10. Wikidata

- component_name: Wikidata
- repository_or_source: https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service
- license: structured data CC0
- maintenance_status: ACTIVE
- last_activity: query-service documentation updated 2026-04-23
- community_size_if_available: global community; exact music subset NOT_MEASURED
- installation_method: SPARQL/REST APIs or full dumps/local Wikibase index
- language_or_runtime: RDF/SPARQL/JSON
- supported_entities: releases, works, people, organizations, awards and external identifiers where contributed
- album_matching_capability: MEDIUM as cross-ID/reference enrichment; data completeness varies
- rating_capability: NO reliable uniform album rating
- professional_review_capability: references/award statements only when sourced
- classical_support: MEDIUM/HIGH for works, catalogues, performers and awards
- jazz_support: MEDIUM for people, releases, awards and external IDs
- popular_music_support: MEDIUM/HIGH
- edition_or_recording_version_support: VARIABLE; not as disciplined as MusicBrainz/Discogs
- API_or_interface_available: SPARQL, REST and dumps
- offline_cache_support: dumps/local indexes
- rate_limit_or_access_constraints: public query-service limits and timeouts
- test_coverage_if_available: service-level project tests; data correctness is community-dependent
- integration_complexity: MEDIUM/HIGH
- recommended_integration_mode: WRAPPER_INTEGRATION

### 11. Gramophone reviews and awards

- component_name: Gramophone
- repository_or_source: https://www.gramophone.co.uk/ and its review/awards archive
- license: proprietary copyrighted editorial content
- maintenance_status: ACTIVE
- last_activity: current reviews and awards available in 2026
- community_size_if_available: publisher reports 42,000+ searchable reviews and 165,000+ combined social followers
- installation_method: website/subscription; no public API found
- language_or_runtime: web and digital archive
- supported_entities: classical recordings, artists, works, awards, comparative recordings
- album_matching_capability: HUMAN/HIGH when catalogue and performer details are present
- rating_capability: editorial recommendations/awards, not a uniform open numeric API
- professional_review_capability: STRONG
- classical_support: STRONG
- jazz_support: LIMITED
- popular_music_support: NO
- edition_or_recording_version_support: STRONG editorially, but structured identifiers are not guaranteed
- API_or_interface_available: no public supported API found
- offline_cache_support: user-owned archive/material only; do not copy subscription content automatically
- rate_limit_or_access_constraints: subscription, copyright and site terms
- test_coverage_if_available: NOT_APPLICABLE/UNKNOWN
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 12. BBC Radio 3 Building a Library

- component_name: BBC Building a Library
- repository_or_source: https://downloads.bbc.co.uk/radio3/building_a_library/ and BBC Radio 3 pages
- license: BBC copyrighted editorial/broadcast content
- maintenance_status: ACTIVE PROGRAM; structured archive continuity varies
- last_activity: programme remains current in 2026; historical 1999-2016 factsheet available
- community_size_if_available: UNKNOWN
- installation_method: web pages, programmes, historical PDF; no public specialist API found
- language_or_runtime: web/audio/PDF
- supported_entities: classical work comparison and recommended recording
- album_matching_capability: HUMAN/HIGH using work, performers, label/catalogue details
- rating_capability: First Choice/recommendation, not a uniform numeric score
- professional_review_capability: STRONG
- classical_support: STRONG
- jazz_support: NO
- popular_music_support: NO
- edition_or_recording_version_support: STRONG editorial intent; machine identifiers inconsistent
- API_or_interface_available: no suitable public API found
- offline_cache_support: user-owned notes or lawful reference import only
- rate_limit_or_access_constraints: BBC rights and availability restrictions
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 13. Diapason and Diapason d'Or

- component_name: Diapason
- repository_or_source: https://www.diapasonmag.fr/disque
- license: proprietary copyrighted editorial content
- maintenance_status: ACTIVE
- last_activity: monthly 2026 review and Diapason d'Or pages present
- community_size_if_available: UNKNOWN
- installation_method: magazine/site/subscription; no public API found
- language_or_runtime: web/print
- supported_entities: classical recordings, reviews, monthly/yearly awards
- album_matching_capability: HUMAN/HIGH with release details
- rating_capability: awards and publication-specific evaluation; preserve raw
- professional_review_capability: STRONG
- classical_support: STRONG
- jazz_support: LIMITED
- popular_music_support: NO
- edition_or_recording_version_support: editorial recording-level distinctions
- API_or_interface_available: no public supported API found
- offline_cache_support: user-owned materials/manual import only
- rate_limit_or_access_constraints: copyright, subscription and French-language metadata
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 14. Fanfare Archive

- component_name: Fanfare
- repository_or_source: https://fanfarearchive.com/
- license: proprietary copyrighted editorial content
- maintenance_status: ACTIVE publication/archive
- last_activity: exact archive activity UNKNOWN; verified available at audit
- community_size_if_available: UNKNOWN
- installation_method: subscription website/print; no public API found
- language_or_runtime: web/print
- supported_entities: classical and some jazz recordings/reviews
- album_matching_capability: HUMAN/HIGH from review bibliographic details
- rating_capability: primarily review evidence, not a uniform open score
- professional_review_capability: STRONG
- classical_support: STRONG
- jazz_support: LIMITED/MEDIUM
- popular_music_support: LOW
- edition_or_recording_version_support: recording-specific editorial review
- API_or_interface_available: no public supported API found
- offline_cache_support: user-owned materials/manual import only
- rate_limit_or_access_constraints: subscription/copyright
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 15. Penguin Guide to Recorded Classical Music

- component_name: Penguin Guide
- repository_or_source: https://www.penguin.co.uk/books/179468/the-penguin-guide-to-the-1000-finest-classical-recordings-by-al-ivan-march-et/9780141399768
- license: copyrighted books
- maintenance_status: HISTORICAL; final broad guide era is no longer continuously updated
- last_activity: 2011/2012 final editions; exact edition must be recorded
- community_size_if_available: 1,000 recordings in final guide; historical annual series
- installation_method: user-owned book/manual structured import
- language_or_runtime: print/ebook
- supported_entities: specific classical recordings and comparative recommendations
- album_matching_capability: HUMAN/HIGH using performers, label and catalogue number
- rating_capability: raw stars and Rosette; preserve edition-specific semantics
- professional_review_capability: STRONG
- classical_support: STRONG
- jazz_support: separate guide lineage, not this component
- popular_music_support: NO
- edition_or_recording_version_support: STRONG but identifiers require manual reconciliation
- API_or_interface_available: NO
- offline_cache_support: user-created index from lawfully owned material
- rate_limit_or_access_constraints: copyright; do not ingest unlicensed scans
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: DATA_IMPORT

### 16. Presto Music awards index

- component_name: Presto Music classical awards index
- repository_or_source: https://www.prestomusic.com/classical/awards
- license: proprietary; terms explicitly prohibit scraping and automated data mining
- maintenance_status: ACTIVE
- last_activity: 2026 award/recommendation pages present
- community_size_if_available: Penguin page lists 400+ cited recordings; broader index size NOT_MEASURED
- installation_method: human reference only unless licensed feed is obtained
- language_or_runtime: web storefront/editorial index
- supported_entities: recordings, works, performers, labels, catalogue numbers and many award systems
- album_matching_capability: HUMAN/HIGH
- rating_capability: aggregates award labels, not a licensed rating API
- professional_review_capability: STRONG reference aggregation
- classical_support: STRONG
- jazz_support: separate storefront sections; not audited as structured API
- popular_music_support: separate storefront sections
- edition_or_recording_version_support: strong catalogue metadata on product pages
- API_or_interface_available: no public licensed API found
- offline_cache_support: NO automated cache under current terms
- rate_limit_or_access_constraints: automated scraping/data mining prohibited
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: legally blocked for automation
- recommended_integration_mode: REFERENCE_ONLY

### 17. DownBeat reviews and polls

- component_name: DownBeat
- repository_or_source: https://downbeat.com/reviews and https://downbeat.com/digitaledition/archive.html
- license: proprietary copyrighted editorial content
- maintenance_status: ACTIVE
- last_activity: 2026 reviews and digital issues present
- community_size_if_available: publication since 1934; exact current readership UNKNOWN
- installation_method: website/digital archive/manual reference; no public API found
- language_or_runtime: web/PDF/print
- supported_entities: jazz albums, books, videos, artists and critics/readers polls
- album_matching_capability: HUMAN/HIGH with artist, title, label and issue context
- rating_capability: 1-5 stars and polls; preserve raw publication evidence
- professional_review_capability: STRONG
- classical_support: LOW
- jazz_support: STRONG
- popular_music_support: LIMITED to covered genres
- edition_or_recording_version_support: reviews can distinguish vintage/reissue/live, but machine identifiers are absent
- API_or_interface_available: no public supported API found
- offline_cache_support: user-owned issues/manual index only
- rate_limit_or_access_constraints: copyright and archive access terms
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 18. All About Jazz

- component_name: All About Jazz
- repository_or_source: https://www.allaboutjazz.com/
- license: proprietary/editor-specific copyrighted content
- maintenance_status: ACTIVE
- last_activity: daily album reviews continue in 2026
- community_size_if_available: reviews published daily since 1997; exact count UNKNOWN
- installation_method: website/manual reference; no stable public review API found
- language_or_runtime: web
- supported_entities: jazz albums, artists, articles, interviews and library lists
- album_matching_capability: HUMAN/MEDIUM-HIGH; structured identifiers are inconsistent
- rating_capability: some reviews carry stars; no uniform supported API established
- professional_review_capability: STRONG
- classical_support: LOW
- jazz_support: STRONG
- popular_music_support: LIMITED
- edition_or_recording_version_support: editorial text may distinguish versions; no canonical hierarchy
- API_or_interface_available: no suitable public API found
- offline_cache_support: manual references only
- rate_limit_or_access_constraints: copyright/site access constraints
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 19. The Jazz Discography

- component_name: The Jazz Discography
- repository_or_source: https://www.lordisco.com/ and institutional access descriptions such as Rutgers Libraries
- license: proprietary subscription database
- maintenance_status: ACTIVE/COMMERCIAL
- last_activity: exact database release date UNKNOWN at audit
- community_size_if_available: UNKNOWN
- installation_method: licensed subscription/database application
- language_or_runtime: proprietary database/web access
- supported_entities: leader, sidemen, session place/date, matrix, issue and catalogue numbers
- album_matching_capability: STRONG specialist session/discographic matching
- rating_capability: NO
- professional_review_capability: NO; source is discography
- classical_support: NO
- jazz_support: STRONG
- popular_music_support: LIMITED
- edition_or_recording_version_support: STRONG issue/reissue and session linkage
- API_or_interface_available: no public API confirmed
- offline_cache_support: licensed product may be local; export rights UNKNOWN
- rate_limit_or_access_constraints: subscription/license
- test_coverage_if_available: UNKNOWN
- integration_complexity: HIGH until licensed export/interface is confirmed
- recommended_integration_mode: REFERENCE_ONLY

### 20. Discography of American Historical Recordings

- component_name: DAHR
- repository_or_source: https://adp.library.ucsb.edu/
- license: mixed source rights; individual public-domain audio is identified, database reuse terms require verification
- maintenance_status: ACTIVE INSTITUTIONAL
- last_activity: public site available and updated for 2026 public-domain status
- community_size_if_available: UNKNOWN
- installation_method: public website; structured export/API not confirmed
- language_or_runtime: institutional web database
- supported_entities: master recordings, takes, matrices, labels, performers and historical issues
- album_matching_capability: STRONG for covered American 78rpm material; not modern album-oriented
- rating_capability: NO
- professional_review_capability: NO
- classical_support: historical recordings where covered
- jazz_support: HIGH for covered historical American recordings
- popular_music_support: historical scope
- edition_or_recording_version_support: STRONG master/take/issue detail
- API_or_interface_available: public search; stable machine API UNKNOWN
- offline_cache_support: UNKNOWN
- rate_limit_or_access_constraints: institutional terms and mixed licensed source data
- test_coverage_if_available: NOT_APPLICABLE/UNKNOWN
- integration_complexity: HIGH
- recommended_integration_mode: REFERENCE_ONLY

### 21. Metacritic music

- component_name: Metacritic
- repository_or_source: https://www.metacritic.com/music/ and official scoring help
- license: proprietary
- maintenance_status: ACTIVE
- last_activity: current 2026 album listings present
- community_size_if_available: UNKNOWN
- installation_method: website/licensing arrangement; no public music API found
- language_or_runtime: web
- supported_entities: selected albums and linked critic reviews
- album_matching_capability: MEDIUM for covered modern releases; identifier interface unavailable
- rating_capability: weighted and normalized 0-100 critic aggregate
- professional_review_capability: STRONG for covered releases
- classical_support: LOW coverage
- jazz_support: LOW/MEDIUM coverage
- popular_music_support: STRONGER but selective and release-date biased
- edition_or_recording_version_support: product-page level; reissues may be distinct products
- API_or_interface_available: no public supported music API found
- offline_cache_support: NO under current evidence
- rate_limit_or_access_constraints: proprietary content/licensing
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: blocked without licensed interface
- recommended_integration_mode: REJECT

### 22. AllMusic/Xperi music metadata

- component_name: AllMusic/Xperi
- repository_or_source: https://www.allmusic.com/ and https://business.tivo.com/products-solutions/metadata/music-metadata
- license: proprietary licensed metadata and editorial content
- maintenance_status: ACTIVE
- last_activity: product and AllMusic documentation current at audit
- community_size_if_available: Xperi advertises 5M+ albums and 40M+ tracks
- installation_method: commercial API/FTP license; human website reference
- language_or_runtime: commercial data feed/API
- supported_entities: artists, albums, songs, credits, styles, moods, reviews and artwork
- album_matching_capability: STRONG commercial IDs/data, subject to contract
- rating_capability: editorial star ratings
- professional_review_capability: STRONG
- classical_support: STRONG commercial taxonomy/editorial coverage
- jazz_support: STRONG commercial taxonomy/editorial coverage
- popular_music_support: STRONG
- edition_or_recording_version_support: commercial entity model; exact semantics require licensed schema review
- API_or_interface_available: commercial API/FTP, not a free public interface
- offline_cache_support: possible only under contract
- rate_limit_or_access_constraints: commercial licensing and content rights
- test_coverage_if_available: UNKNOWN
- integration_complexity: MEDIUM technically, HIGH procurement/legal
- recommended_integration_mode: OPTIONAL_DEPENDENCY

### 23. Rate Your Music

- component_name: Rate Your Music
- repository_or_source: https://rateyourmusic.com/
- license: proprietary user/community content
- maintenance_status: ACTIVE
- last_activity: service active at audit; exact release activity UNKNOWN
- community_size_if_available: large community, exact verified count UNKNOWN
- installation_method: website only; no public supported API found
- language_or_runtime: web
- supported_entities: releases, recordings, charts, genres, lists and community ratings
- album_matching_capability: strong human database, unavailable supported machine interface
- rating_capability: community ratings and weighted charts
- professional_review_capability: community reviews, not a professional-review API
- classical_support: MEDIUM/HIGH community coverage but recording identity automation is unsafe
- jazz_support: HIGH community coverage but edition matching is unsafe without an interface
- popular_music_support: STRONG
- edition_or_recording_version_support: site distinguishes releases, but integration contract unavailable
- API_or_interface_available: no public API; automated access is prohibited by published terms summaries/evidence
- offline_cache_support: NO lawful automated path established
- rate_limit_or_access_constraints: automated crawling/access prohibited without permission
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: legally blocked
- recommended_integration_mode: REJECT

### 24. Album of the Year

- component_name: Album of the Year
- repository_or_source: https://www.albumoftheyear.org/
- license: proprietary; user content remains user-owned with a license granted to the service
- maintenance_status: ACTIVE
- last_activity: terms updated 2026-02-25; active 2026 changelog
- community_size_if_available: UNKNOWN
- installation_method: website only; no public supported API found
- language_or_runtime: web
- supported_entities: albums, critic aggregates, user ratings, lists and reviews
- album_matching_capability: MEDIUM for covered releases; no supported machine IDs/interface
- rating_capability: critic and user aggregates
- professional_review_capability: YES as an aggregator, but no licensed API
- classical_support: LOW
- jazz_support: LOW/MEDIUM
- popular_music_support: STRONGER
- edition_or_recording_version_support: site-specific release rules; no integration contract
- API_or_interface_available: no public supported API found
- offline_cache_support: NO
- rate_limit_or_access_constraints: terms and anti-automation controls; permission required
- test_coverage_if_available: NOT_APPLICABLE
- integration_complexity: legally/operationally blocked
- recommended_integration_mode: REJECT

## Integration order

No implementation is authorized by this audit alone. The next minimum validation sequence is:

1. CritiqueBrainz: measure exact MusicBrainz-ID coverage on a fixed multilingual/classical/jazz sample; verify per-record licenses, average/count semantics, stale behavior and rate limits.
2. Existing identity layer: compare current custom MusicBrainz/Discogs matching with beets/Picard outputs on difficult editions. Reuse stronger behavior instead of duplicating it.
3. Classical identity: prototype a non-scoring `recording_identity` using MusicBrainz relationships; evaluate DOREMUS/Open Opus only as enrichment/local indexes.
4. Jazz identity: prototype a non-scoring master/session/release graph using MusicBrainz and Discogs fields; manually benchmark against The Jazz Discography/DAHR references.
5. TheAudioDB: test whether score provenance, vote counts, MBID mapping and terms satisfy the rating evidence contract. Keep disabled until then.
6. Professional sources: support only user-owned structured imports or licensed feeds. Preserve raw awards/reviews and never infer a numeric score from prose, snippets or badges.

## Hard rejection gates

A source cannot enter `music_score` unless all are true:

- a stable authorized API, dump, licensed feed, or user-owned lawful import exists;
- the exact canonical entity and edition/recording relationship are verified;
- the raw rating/award, scale, count, source URL, retrieval time, adapter version and cache artifact are retained;
- popularity, sales, chart position, listeners, plays and ownership counts are excluded;
- missing coverage is `INSUFFICIENT_DATA`, never zero;
- classical and jazz specialist evidence remains separate until a reviewed conversion rule exists;
- fixture tests cover exact, ambiguous, edition-conflict, sparse-vote, unavailable and stale-cache cases.

## Primary evidence index

- MusicBrainz API, entities, ratings and limits: https://musicbrainz.org/doc/MusicBrainz_API
- MusicBrainz release/edition semantics: https://musicbrainz.org/doc/Release
- MusicBrainz data license: https://musicbrainz.org/doc/About/Data_License
- CritiqueBrainz API: https://critiquebrainz.readthedocs.io/api/endpoints.html
- beets ParentWork: https://beets.readthedocs.io/en/latest/plugins/parentwork.html
- Picard plugins: https://picard.musicbrainz.org/plugins/
- Open Opus scope and dump: https://openopus.org/ and https://github.com/openopus-org/openopus_api
- DOREMUS dataset: https://data.doremus.org/
- Wikidata query service and license: https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service
- AcoustID/Chromaprint: https://github.com/acoustid/chromaprint and https://acoustid.org/license
- TheAudioDB API/terms: https://www.theaudiodb.com/free_music_api and https://www.theaudiodb.com/docs_terms_of_use.php
- Gramophone archive facts: https://www.gramophone.co.uk/
- BBC Building a Library archive: https://downloads.bbc.co.uk/radio3/building_a_library/baL_factsheet_1999-2016.pdf
- Diapason recording pages: https://www.diapasonmag.fr/disque
- Penguin Guide publisher page: https://www.penguin.co.uk/books/179468/the-penguin-guide-to-the-1000-finest-classical-recordings-by-al-ivan-march-et/9780141399768
- Presto terms and awards index: https://www.prestomusic.com/terms and https://www.prestomusic.com/classical/awards
- DownBeat reviews/archive: https://downbeat.com/reviews and https://downbeat.com/digitaledition/archive.html
- All About Jazz: https://www.allaboutjazz.com/
- DAHR: https://adp.library.ucsb.edu/
- Metacritic scoring method: https://metacritichelp.zendesk.com/hc/en-us/articles/14478499933079-How-do-you-compute-METASCORES
- AllMusic/Xperi provenance: https://www.allmusic.com/product-submissions and https://business.tivo.com/products-solutions/metadata/music-metadata
- Album of the Year terms: https://www.albumoftheyear.org/terms-of-use/

Audit status: `MUSIC_RATING_ECOSYSTEM_AUDIT_COMPLETE`
