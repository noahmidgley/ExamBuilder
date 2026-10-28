"""
Fixed topic and sub-topic lists, checked against the current specifications:
  - AQA A-level Geography 7037 specification (sections 3.1 and 3.2)
  - OCR A Level Computer Science H446 specification, version 3.0 (sections 1 and 2)

Sub-topics are the spec's numbered sub-sections. Two exceptions:
  - Geography 3.2.2.2 is split into its two parts (3.2.2.2.1 / 3.2.2.2.2).
  - CS 2.3 has only one sub-section (2.3.1 Algorithms), which is too broad to
    filter by, so it's split using the spec's own lettered points (a)-(f).

The tagger (Claude) may ONLY choose from these lists, and the website uses
them for the filter menus. Changing a name here won't rename questions that
are already stored, so avoid renaming once you've imported papers.
"""

TOPICS = {
    "geography": {
        "P1": {
            "Water and carbon cycles": [
                "3.1.1.1 Water and carbon cycles as natural systems",
                "3.1.1.2 The water cycle",
                "3.1.1.3 The carbon cycle",
                "3.1.1.4 Water, carbon, climate and life on Earth",
                "3.1.1.5 Quantitative and qualitative skills",
                "3.1.1.6 Case studies",
            ],
            "Hot desert systems and landscapes": [
                "3.1.2.1 Deserts as natural systems",
                "3.1.2.2 Systems and processes",
                "3.1.2.3 Arid landscape development in contrasting settings",
                "3.1.2.4 Desertification",
                "3.1.2.5 Quantitative and qualitative skills",
                "3.1.2.6 Case studies",
            ],
            "Coastal systems and landscapes": [
                "3.1.3.1 Coasts as natural systems",
                "3.1.3.2 Systems and processes",
                "3.1.3.3 Coastal landscape development",
                "3.1.3.4 Coastal management",
                "3.1.3.5 Quantitative and qualitative skills",
                "3.1.3.6 Case studies",
            ],
            "Glacial systems and landscapes": [
                "3.1.4.1 Glaciers as natural systems",
                "3.1.4.2 The nature and distribution of cold environments",
                "3.1.4.3 Systems and processes",
                "3.1.4.4 Glaciated landscape development",
                "3.1.4.5 Human impacts on cold environments",
                "3.1.4.6 Quantitative and qualitative skills",
                "3.1.4.7 Case studies",
            ],
            "Hazards": [
                "3.1.5.1 The concept of hazard in a geographical context",
                "3.1.5.2 Plate tectonics",
                "3.1.5.3 Volcanic hazards",
                "3.1.5.4 Seismic hazards",
                "3.1.5.5 Storm hazards",
                "3.1.5.6 Fires in nature",
                "3.1.5.7 Case studies",
            ],
            "Ecosystems under stress": [
                "3.1.6.1 Ecosystems and sustainability",
                "3.1.6.2 Ecosystems and processes",
                "3.1.6.3 Biomes",
                "3.1.6.4 Ecosystems in the British Isles over time",
                "3.1.6.5 Marine ecosystems",
                "3.1.6.6 Local ecosystems",
                "3.1.6.7 Case studies",
            ],
        },
        "P2": {
            "Global systems and global governance": [
                "3.2.1.1 Globalisation",
                "3.2.1.2 Global systems",
                "3.2.1.3 International trade and access to markets",
                "3.2.1.4 Global governance",
                "3.2.1.5 The 'global commons' (incl. Antarctica)",
                "3.2.1.6 Globalisation critique",
                "3.2.1.7 Quantitative and qualitative skills",
            ],
            "Changing places": [
                "3.2.2.1 The nature and importance of places",
                "3.2.2.2.1 Relationships and connections",
                "3.2.2.2.2 Meaning and representation",
                "3.2.2.3 Quantitative and qualitative skills",
                "3.2.2.4 Place studies",
            ],
            "Contemporary urban environments": [
                "3.2.3.1 Urbanisation",
                "3.2.3.2 Urban forms",
                "3.2.3.3 Social and economic issues associated with urbanisation",
                "3.2.3.4 Urban climate",
                "3.2.3.5 Urban drainage",
                "3.2.3.6 Urban waste and its disposal",
                "3.2.3.7 Other contemporary urban environmental issues",
                "3.2.3.8 Sustainable urban development",
                "3.2.3.9 Case studies",
            ],
            "Population and the environment": [
                "3.2.4.1 Introduction",
                "3.2.4.2 Environment and population",
                "3.2.4.3 Environment, health and well-being",
                "3.2.4.4 Population change",
                "3.2.4.5 Principles of population ecology",
                "3.2.4.6 Global population futures",
                "3.2.4.7 Case studies",
            ],
            "Resource security": [
                "3.2.5.1 Resource development",
                "3.2.5.2 Natural resource issues",
                "3.2.5.3 Water security",
                "3.2.5.4 Energy security",
                "3.2.5.5 Mineral security",
                "3.2.5.6 Resource futures",
                "3.2.5.7 Case studies",
            ],
        },
    },
    "computer-science": {
        "C01": {
            "1.1 Processors, input, output and storage": [
                "1.1.1 Structure and function of the processor",
                "1.1.2 Types of processor",
                "1.1.3 Input, output and storage",
            ],
            "1.2 Software and software development": [
                "1.2.1 Systems software",
                "1.2.2 Applications generation",
                "1.2.3 Software development",
                "1.2.4 Types of programming language",
            ],
            "1.3 Exchanging data": [
                "1.3.1 Compression, encryption and hashing",
                "1.3.2 Databases",
                "1.3.3 Networks",
                "1.3.4 Web technologies",
            ],
            "1.4 Data types, data structures and algorithms": [
                "1.4.1 Data types",
                "1.4.2 Data structures",
                "1.4.3 Boolean algebra",
            ],
            "1.5 Legal, moral, cultural and ethical issues": [
                "1.5.1 Computing related legislation",
                "1.5.2 Moral and ethical issues",
            ],
        },
        "C02": {
            "2.1 Elements of computational thinking": [
                "2.1.1 Thinking abstractly",
                "2.1.2 Thinking ahead",
                "2.1.3 Thinking procedurally",
                "2.1.4 Thinking logically",
                "2.1.5 Thinking concurrently",
            ],
            "2.2 Problem solving and programming": [
                "2.2.1 Programming techniques",
                "2.2.2 Computational methods",
            ],
            "2.3 Algorithms": [
                "2.3.1 Analysis, design and suitability of algorithms",
                "2.3.1 Complexity and Big O notation",
                "2.3.1 Data structure algorithms (stacks, queues, trees, linked lists)",
                "2.3.1 Sorting algorithms",
                "2.3.1 Searching algorithms",
                "2.3.1 Shortest path algorithms (Dijkstra, A*)",
            ],
        },
    },
}

# The spec's full title for CS 1.1, shown to the tagger for context.
FULL_TITLES = {
    "1.1 Processors, input, output and storage":
        "1.1 The characteristics of contemporary processors, input, output and storage devices",
}


def topics_for(subject, paper):
    """{topic: [subtopics]} for one paper, e.g. topics_for('geography', 'P1')."""
    return TOPICS[subject][paper]


def all_topics(subject):
    """{topic: [subtopics]} across both papers of a subject (for filter menus)."""
    merged = {}
    for paper_topics in TOPICS[subject].values():
        merged.update(paper_topics)
    return merged


def is_valid(subject, paper, topic, subtopic):
    return subtopic in TOPICS[subject][paper].get(topic, [])
