class Score:
    def __init__(self):
        self.score = 10.0
        self.items = []

    def minus(self, value: float, text: str):
        self.score -= value
        self.items.append({"value": value,"text": text})

    def result(self):
        if self.score < 0:
            self.score = 0.0

        final_score = round(self.score, 1)

        if final_score >= 9:
            title = "Excellent"
            description = "Your email is perfect"
        elif final_score >= 8:
            title = "Good"
            description = "Your email is almost perfect"
        elif final_score >= 6:
            title = "Average"
            description = "Your email is okay but could be improved"
        else:
            title = "Bad"
            description = "Your email will likely go to spam"

        return {"score": final_score,"title": title,"description": description,"items": self.items}
