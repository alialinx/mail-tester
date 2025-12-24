class Score:
    def __init__(self):
        self.start = 10.0
        self.items = []
        self._score = self.start

    def minus(self, value: float, text: str, code: str = None, severity: str = "warning", details: str = "", how_to_fix: str = ""):
        self._score -= float(value)
        self.items.append({
            "code": code or "GENERIC",
            "severity": severity,
            "points": -float(value),
            "title": text,
            "details": details,
            "how_to_fix": how_to_fix
        })

    def result(self):
        score = max(0.0, round(self._score, 2))

        if score >= 9:
            title, desc = "Excellent", "Your email is perfect"
        elif score >= 7:
            title, desc = "Good", "Your email is good"
        elif score >= 5:
            title, desc = "Average", "Your email has issues"
        else:
            title, desc = "Poor", "Your email is likely to fail deliverability checks"

        return {"score": score,"title": title,"description": desc,"items": [{"value": abs(i["points"]), "text": i["title"]} for i in self.items],"issues": self.items,}
