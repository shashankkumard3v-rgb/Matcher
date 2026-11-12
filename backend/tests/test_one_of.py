""""Java or Go or C++" means ANY ONE of them: a developer shouldn't be scored down (or asked)
for every language in the list."""
from app.config import weights_config
from app.gate.gate import make_questions
from app.library.match import match
from app.library.profile import ResumeProfile, SkillLevel
from app.parsing.structure import normalize_one_of
from app.schemas.models import Experience, JobDescription, Resume, ScoreResult
from app.scoring.deterministic import score_deterministic
from app.scoring.synonyms import alternatives, canonical, count_mentions


def test_alternatives_are_read_from_how_jds_write_them():
    assert alternatives("Java or Go or C++") == ["Java", "Go", "C++"]
    assert alternatives("Java, Go, or C++") == ["Java", "Go", "C++"]
    assert alternatives("Experience with AWS or GCP") == ["AWS", "GCP"]
    assert alternatives("One of Java, Go or C++") == ["Java", "Go", "C++"]
    assert alternatives("Kafka or similar") == ["Kafka"]          # vague extras are dropped
    assert alternatives("CI/CD") == ["CI/CD"] and alternatives("REST APIs") == ["REST APIs"]
    assert canonical("Golang or Rust") == "Go or Rust"


def test_parser_output_is_normalized():
    assert normalize_one_of(["Java/Go/C++", "CI/CD", "Kafka or similar", "Java, Go or C++"]) == \
        ["Java or Go or C++", "CI/CD", "Kafka"]                    # the duplicate group is merged


def _resume(*bullets, skills=()):
    return Resume(experience=[Experience(title="Engineer", company="Acme", start="2019-01", end="present",
                                         bullets=list(bullets))], skills=list(skills))


def _rules(resume, jd):
    cfg = weights_config()
    dims, must, *_ = score_deterministic(resume, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    return {d.key: d for d in dims}, must


def test_one_language_is_enough_for_full_credit():
    jd = JobDescription(title="Backend Engineer", must_have=["Java or Go or C++", "Docker"])
    resume = _resume("Built payment services in Java on Docker, cutting latency by 40%")
    dims, must = _rules(resume, jd)
    group = must[0]
    assert group.status == "evidenced" and group.credit == 1.0 and group.matched == "Java"
    assert "Any one of these is enough" in group.note
    assert dims["must_have_coverage"].points == dims["must_have_coverage"].weight   # nothing lost


def test_mentioning_several_options_is_not_keyword_stuffing():
    jd = JobDescription(must_have=["Java or Go or C++"])
    resume = _resume("Built Java services", "Moved Java jobs to Go", "Wrote Go CLIs", "Java and Go code reviews")
    _, must = _rules(resume, jd)
    assert must[0].credit == 1.0                    # Java 3x + Go 3x isn't one skill said 6 times


def test_missing_group_asks_for_one_option_not_all():
    jd = JobDescription(must_have=["Java or Go or C++"])
    resume = _resume("Built Python services")
    cfg = weights_config()
    dims, must, nice, years, _ = score_deterministic(resume, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    s = ScoreResult(total=0, deterministic_points=0, llm_points=0, dimensions=dims, must_have=must, nice_to_have=nice,
           years_found=years, top_losses=[], warnings=[], pins={})
    q = make_questions(s, s, jd, [], cfg["weights"])[0]
    assert "ONE of these: Java, Go or C++" in q.text and "don't need all" in q.text


def test_quick_match_counts_the_option_you_have():
    jd = JobDescription(title="Engineer", must_have=["Java or Go or C++"])
    profile = ResumeProfile(skills=[SkillLevel(name="Go", level=4, last_used="present")])
    r = match(profile, _resume("Built Go services"), jd)
    assert r.required[0].matched == "Go" and r.required[0].level == 4


def test_count_mentions_of_a_group():
    assert count_mentions("Java or Go or C++", "Built Java services. Shipped Go tools.") == 2
    assert count_mentions("Java or Go or C++", "Let's go home") == 0   # "go" the word isn't Go


# ---------- all of / any one / N of / one specific, and related languages ----------

def _hits(jd_must, *bullets, skills=()):
    _, must = _rules(_resume(*bullets, skills=skills), JobDescription(title="Engineer", must_have=jd_must))
    return {h.skill: h for h in must}


def test_all_of_needs_each_one():
    h = _hits(["Java", "Spring Boot"], "Built Java services")          # "Java and Spring Boot"
    assert h["Java"].status == "evidenced" and h["Spring Boot"].status == "missing"


def test_one_specific_skill_only_counts_itself():
    h = _hits(["Go"], "Built Java and C++ services")
    assert h["Go"].status == "missing"                                 # other languages don't count


def test_n_of_counts_the_best_n():
    one = _hits(["2 of Java or Go or C++"], "Built Java services")["2 of Java or Go or C++"]
    assert one.status == "partial" and one.credit == 0.5 and "wants 2 of these" in one.note
    two = _hits(["2 of Java or Go or C++"], "Built Java services and Go CLIs")["2 of Java or Go or C++"]
    assert two.status == "evidenced" and two.credit == 1.0 and two.matched == "Java + Go"


def test_typescript_shows_javascript_but_not_the_other_way():
    js = _hits(["JavaScript"], "Built the dashboard in TypeScript")["JavaScript"]
    assert js.status == "evidenced" and "through TypeScript" in js.note
    ts = _hits(["TypeScript"], "Built the dashboard in JavaScript")["TypeScript"]
    assert ts.status == "close" and 0 < ts.credit < 1 and ts.matched == "JavaScript"


def test_node_counts_as_node_js_and_shows_javascript():
    h = _hits(["Node.js", "JavaScript"], "Built REST APIs in Node and Express")
    assert h["Node.js"].status == "evidenced" and h["JavaScript"].status == "evidenced"
    assert _hits(["Node.js"], "Each graph node stores a value")["Node.js"].status == "missing"


def test_slashes_mean_any_language_but_both_for_a_stack():
    assert normalize_one_of(["Java/Go/C++", "Node.js/TypeScript", "Python, SQL", "at least two of Java, Go, C++"]) == \
        ["Java or Go or C++", "Node.js", "TypeScript", "Python", "SQL", "2 of Java or Go or C++"]


def test_questions_for_close_and_partial():
    cfg = weights_config()
    jd = JobDescription(must_have=["TypeScript", "2 of Java or Go or C++"])
    dims, must, nice, years, _ = score_deterministic(
        _resume("Built the UI in JavaScript", "Built Java services"), jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    s = ScoreResult(total=0, deterministic_points=0, llm_points=0, dimensions=dims, must_have=must, nice_to_have=nice,
                    years_found=years, top_losses=[], warnings=[], pins={})
    texts = {q.id: q.text for q in make_questions(s, s, jd, [], cfg["weights"])}
    assert "your resume shows JavaScript, which is related but not the same" in texts["close:typescript"]
    assert "wants 2 of these: Java, Go, C++" in texts["partial:2 of java or go or c++"]


def test_quick_match_n_of_and_related():
    jd = JobDescription(title="Engineer", must_have=["2 of Java or Go or C++", "JavaScript"])
    profile = ResumeProfile(skills=[SkillLevel(name="Java", level=4, last_used="present"),
                                    SkillLevel(name="TypeScript", level=3, last_used="present")])
    r = match(profile, _resume("x"), jd)
    n_of, js = r.required
    assert n_of.credit == 0.4 and "1 of 2 needed" in n_of.note          # (4/5 + 0) / 2
    assert js.matched == "TypeScript"                                   # TypeScript shows JavaScript
